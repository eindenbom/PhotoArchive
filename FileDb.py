# -*- coding: utf-8 -*-
import fnmatch
import hashlib
import pathlib
import re
import secrets
from os import scandir
from sys import stderr
from time import strftime
from typing import Dict, List, Optional, Pattern, Union


class FileInfo:
    __slots__ = ["__filePath", "__checksum", "__duplicate", "__id"]

    __filePath: pathlib.Path
    __checksum: str
    __id: int
    __duplicate: "FileInfo"

    # noinspection PyShadowingBuiltins
    def __init__( self, filePath: pathlib.Path, checksum: str, id: int ):
        self.__filePath = filePath
        self.__checksum = checksum
        self.__id = id
        self.__duplicate = None

    @property
    def filePath( self ):
        return self.__filePath

    @property
    def checksum( self ):
        return self.__checksum

    @property
    def id( self ):
        return self.__id

    @property
    def duplicate( self ):
        return self.__duplicate

    @duplicate.setter
    def duplicate( self, other: "FileInfo" ):
        self.__duplicate = other

    def findBestMatch( self, filePath: pathlib.Path ):
        if self.duplicate is None:
            return self

        # есть файлы с одинаковой чек-суммой, ищем первый совпадающий по имени
        fileName = filePath.name.lower()

        f = self
        while f is not None:
            if f.filePath.name.lower() == fileName:
                return f

            f = f.duplicate

        return self


class FileDb:
    __hashIndex: Dict[str, FileInfo]
    __algorithms: List[str]

    def __init__( self ):
        self.__hashIndex = dict()
        self.__nextId = 0
        self.__algorithms = []

    @property
    def algorithms( self ):
        return self.__algorithms

    def hasAlgorithm( self, algorithm: str ):
        return algorithm in self.__algorithms

    def addFile( self, filePath: pathlib.Path, checksum: str ):
        algorithm = detectChecksumAlgorithm( checksum )
        if algorithm is None:
            raise ValueError( 'Unknown checksum type' )

        if algorithm not in self.__algorithms:
            self.__algorithms.append( algorithm )

        fileInfo = FileInfo( filePath, checksum, self.__nextId )
        prevInfo = self.__hashIndex.get( checksum, None )
        if prevInfo is None:
            self.__hashIndex[checksum] = fileInfo
        else:
            while prevInfo.duplicate is not None:
                prevInfo = prevInfo.duplicate

            prevInfo.duplicate = fileInfo

        self.__nextId += 1

    def addChecksumFile( self, basePath: Optional[pathlib.Path], fileName: pathlib.Path ):
        with ChecksumFileReader( fileName ) as reader:
            for fp, c in reader:
                if basePath is not None:
                    fp = basePath.joinpath( fp )

                self.addFile( fp, c )

    def addIndexedTree( self, basePath: pathlib.Path, relativePath: pathlib.Path = None ):
        if relativePath is None:
            relativePath = pathlib.Path()
        self.__addIndexedTree( basePath, relativePath )

    def __addIndexedTree( self, basePath: pathlib.Path, relativePath: pathlib.Path ):
        folderPath = basePath.joinpath( relativePath )
        for indexFileName in ('Checksums.sha2', 'Checksums.sha1'):
            indexFilePath = folderPath.joinpath( indexFileName )
            if indexFilePath.exists():
                self.addChecksumFile( relativePath, indexFilePath )
                return

        with scandir( folderPath ) as it:
            for fileEntry in it:
                if fileEntry.is_dir():
                    self.__addIndexedTree( basePath, relativePath.joinpath( fileEntry.name ) )

    def get( self, checksum: str ):
        return self.__hashIndex.get( checksum, None )

    def findFile( self, filePath: pathlib.Path ):
        fileInfo = self.__findFileInfoChain( filePath )
        if fileInfo is not None:
            fileInfo = fileInfo.findBestMatch( filePath )

        return fileInfo

    def __findFileInfoChain( self, filePath: pathlib.Path ):
        for algorithm in self.algorithms:
            fileInfo = self.get( calculateChecksum( filePath, algorithm ) )
            if fileInfo is not None:
                return fileInfo

        return None

    def entries( self ):
        return self.__hashIndex.items()


defaultChecksumAlgorithm = 'sha256'


def detectChecksumAlgorithm( checksum: str ):
    if len( checksum ) == 40:
        return 'sha1'
    if len( checksum ) == 64:
        return 'sha256'

    return None


def calculateChecksum( filePath: pathlib.Path, algorithm: str = defaultChecksumAlgorithm ):
    h = hashlib.new( algorithm )
    with filePath.open( mode = 'rb', buffering = False ) as file:
        try:
            while True:
                data = file.read( 0x10000 )
                if len( data ) == 0:
                    break

                h.update( data )
        except IOError as e:
            e.filename = str( filePath )
            raise

        file.close()

    return h.hexdigest()


class FileTreeIterator:
    __excluded: List[Pattern]

    def __init__( self ):
        self.__excluded = []

    def addExcluded( self, *glob: str ):
        for g in glob:
            self.__excluded.append(
                re.compile( fnmatch.translate( g ),
                            re.RegexFlag.IGNORECASE | re.RegexFlag.DOTALL ) )

    def iterate( self, basePath: pathlib.Path ):
        iteratorStack = list()
        subdir = pathlib.Path()
        iterator = self.__scanDir( basePath )
        while True:
            try:
                entry = next( iterator )
                filePath = subdir.joinpath( entry.name )
                if not entry.is_dir():
                    yield filePath
                else:
                    iteratorStack.append( iterator )
                    iterator = self.__scanDir( basePath.joinpath( filePath ) )
                    subdir = filePath

            except StopIteration:
                if len( iteratorStack ) == 0:
                    break

                subdir = subdir.parent
                iterator = iteratorStack.pop()

    def __scanDir( self, folder: pathlib.Path ):
        return iter( sorted(
            filter( lambda x: self.__checkName( x.name ),
                    scandir( folder ) ), key = lambda x: x.name.lower() ) )

    def __checkName( self, name: str ):
        for e in self.__excluded:
            if e.match( name ):
                return False

        return True


class ChecksumFileReader:
    def __init__( self, filePath: Union[str, pathlib.PurePath] ):
        self.__file = open( filePath, mode = 'rt', encoding = 'utf-8' )
        self.__lineNo = 0

    def __enter__( self ):
        return self

    def __exit__( self, exc_type, exc_val, exc_tb ):
        self.close()

    def close( self ):
        self.__file.close()

    def __iter__( self ):
        return self

    def __next__( self ):
        while True:
            try:
                l = self.__file.readline()
            except UnicodeDecodeError as e:
                fileName = self.__file.name
                raise IOError( f'{fileName}: {e}' ) from e

            if l == '':
                raise StopIteration

            self.__lineNo += 1
            l = l.rstrip( '\r\n' )
            if l == '':
                continue

            c, s, n = l.partition( ' ' )
            if n != '' and (n[0] == '*' or n[0] == ' '):
                n = n[1:]

            if s == '' or n == '':
                lineNo = self.__lineNo
                fileName = self.__file.name
                raise ValueError( f'Invalid checksum line #{lineNo} in {fileName}' )

            return pathlib.Path( n ), c

    @property
    def filePath( self ):
        return pathlib.Path( self.__file.name )


class ChecksumFileWriter:
    def __init__( self, filePath: Union[str, pathlib.PurePath] ):
        self.__file = open( filePath, mode = 'wt', encoding = 'utf-8' )

    def __enter__( self ):
        return self

    def __exit__( self, exc_type, exc_val, exc_tb ):
        self.close()

    def close( self ):
        self.__file.close()

    def write( self, filePath: pathlib.PurePath, checksum: str ):
        print( checksum, ' *./', filePath.as_posix(), file = self.__file, sep = '' )

    @property
    def filePath( self ):
        return pathlib.Path( self.__file.name )


class IndexValidationError( Exception ):
    pass


class IndexBuilder:
    __oldIndexFilePath: Optional[pathlib.Path]
    __fileChecksumMap: Dict[pathlib.Path, str]
    __newIndexFilePath: Optional[pathlib.Path]
    __newIndexWriter: Optional[ChecksumFileWriter]

    def __init__( self, *, folder: pathlib.Path, fileTreeIterator: FileTreeIterator,
                  create: bool = False, verify: False,
                  indexFileName: Optional[pathlib.Path] = None,
                  rejectChanges: bool = True, reviewChanges: bool = True,
                  reuseChecksums: bool = False ):
        self.__fileTreeIterator = fileTreeIterator
        self.__indexFileName = indexFileName
        self.__basePath = folder

        self.__create = create
        self.__verify = verify

        self.__rejectChanges = rejectChanges
        self.__reviewChanges = reviewChanges
        self.__reuseChecksums = reuseChecksums

        self.__newIndexFilePath = None
        self.__newIndexWriter = None

        self.__oldIndexFilePath = None
        self.__fileChecksumMap = dict()

        self.__newCount = 0
        self.__missingCount = 0
        self.__damagedCount = 0

    @property
    def folderName( self ):
        return self.__basePath.as_posix()

    def run( self ):
        assert self.__create or self.__verify
        try:
            rc = self.__process()
        finally:
            self.__cleanup()

        missing = self.__missingCount
        damaged = self.__damagedCount
        new = self.__newCount
        if missing > 0 or damaged > 0 or new > 0:
            print( f'{self.folderName}: damaged = {damaged}, missing = {missing}, new = {new}' )
        else:
            print( f'{self.folderName}: OK' )

        return rc

    def __prettyFileName( self, path: pathlib.Path ):
        fullPath = self.__basePath.joinpath( path )
        prettyName = fullPath.as_posix()
        if not fullPath.is_absolute():
            prettyName = './' + prettyName
        return prettyName

    def __process( self ):
        if self.__verify:
            self.__readOldIndex()

        fileList = sorted( self.__fileTreeIterator.iterate( self.__basePath ) )
        if self.__verify:
            self.__checkMissing( fileList )

        for fp in fileList:
            self.__processFile( fp )

        if not self.__create:
            return self.__missingCount == 0 and self.__damagedCount == 0
        else:
            return self.__commitNewIndex()

    def __getIndexFilePath( self ):
        if self.__indexFileName is not None:
            fileName = self.__indexFileName
        else:
            fileName = 'Checksums.sha2'

        return self.__basePath.joinpath( fileName )

    def __openOldIndex( self ):
        filePath = self.__getIndexFilePath()
        try:
            return ChecksumFileReader( filePath )
        except FileNotFoundError as e:
            if self.__indexFileName is None:
                for name in ('Checksums.sha1',):
                    try:
                        return ChecksumFileReader( self.__basePath.joinpath( name ) )
                    except FileNotFoundError:
                        continue

            if not self.__create:
                raise

            print( 'warning:', e, file = stderr )
            return None

    def __readOldIndex( self ):
        reader = self.__openOldIndex()
        if reader is None:
            return

        with reader:
            self.__oldIndexFilePath = reader.filePath
            for fp, c in reader:
                self.__fileChecksumMap[fp] = c

            reader.close()

    def __raiseValidationError( self ):
        raise IndexValidationError( f'{self.folderName}: validation failed, aborting' )

    def __checkMissing( self, fileList: List[pathlib.Path] ):
        if len( self.__fileChecksumMap ) == 0:
            return

        fileSet = set( fileList )
        for fp in sorted( self.__fileChecksumMap.keys() ):
            if fp in fileSet:
                continue

            print( 'm', self.__prettyFileName( fp ) )
            self.__missingCount += 1

        if self.__rejectChanges and self.__missingCount > 0:
            self.__raiseValidationError()

    def __processFile( self, filePath: pathlib.Path ):
        checksum = None
        algorithm = None

        fullFilePath = self.__basePath.joinpath( filePath )
        if self.__verify:
            reference = self.__fileChecksumMap.get( filePath, None )
            if reference is None:
                self.__newCount += 1
                print( 'n', self.__prettyFileName( filePath ) )
            else:
                refAlgorithm = detectChecksumAlgorithm( reference )
                if self.__reuseChecksums:
                    algorithm = refAlgorithm
                    checksum = reference
                else:
                    if refAlgorithm != algorithm:
                        algorithm = refAlgorithm
                        checksum = calculateChecksum( fullFilePath, algorithm )

                    if checksum != reference:
                        self.__damagedCount += 1
                        print( 'd', self.__prettyFileName( filePath ) )
                        if self.__rejectChanges:
                            self.__raiseValidationError()

        if self.__create:
            self.__openNewIndex()

            if algorithm != defaultChecksumAlgorithm:
                algorithm = defaultChecksumAlgorithm
                checksum = calculateChecksum( fullFilePath, algorithm )

            self.__newIndexWriter.write( filePath, checksum )

    def __openNewIndex( self ):
        if self.__newIndexFilePath is not None:
            return

        filePath = self.__getIndexFilePath()

        stem = filePath.stem + strftime( '-%Y%m%d_%H%M%S' )
        suffix = filePath.suffix

        newFilePath = filePath.with_name( stem + suffix )
        attempt = 0
        while True:
            if not newFilePath.exists():
                break

            attempt += 1
            if attempt >= 50:
                raise FileExistsError( f'Unable to create new file in {newFilePath.parent}' )

            newFilePath = newFilePath.with_name( stem + '_' + secrets.token_hex( 2 ) + suffix )

        self.__newIndexWriter = ChecksumFileWriter( newFilePath )
        self.__newIndexFilePath = newFilePath

    def __commitNewIndex( self ):
        self.__closeNewIndexWriter()

        if self.__newIndexFilePath is None:
            print( f'{self.folderName}: no files found, index not created' )

        success = self.__missingCount == 0 and self.__damagedCount == 0
        if not success and self.__reviewChanges:
            newIndexFile = self.__newIndexFilePath
            if newIndexFile is not None:
                self.__newIndexFilePath = None
                print( f'{self.folderName}: new index: {newIndexFile.as_posix()}' )

            return False
        else:
            self.__removeOldIndex( not success )
            self.__renameNewIndex()
            return True

    def __removeOldIndex( self, makeBackup: bool ):
        oldIndexFile = self.__oldIndexFilePath
        if oldIndexFile is None:
            return

        self.__oldIndexFilePath = None
        if not makeBackup:
            oldIndexFile.unlink()
            return

        stem = oldIndexFile.stem
        suffix = oldIndexFile.suffix

        backupFileName = oldIndexFile.with_name( stem + '.bak' + suffix )
        try:
            backupFileName.unlink()
        except FileNotFoundError:
            pass

        oldIndexFile.rename( backupFileName )

    def __renameNewIndex( self ):
        newIndexFile = self.__newIndexFilePath
        if newIndexFile is None:
            return

        self.__closeNewIndexWriter()

        self.__newIndexFilePath = None
        newIndexFile.rename( self.__getIndexFilePath() )

    def __closeNewIndexWriter( self, nothrow: bool = False ):
        newIndexWriter = self.__newIndexWriter
        if newIndexWriter is None:
            return

        self.__newIndexWriter = None
        try:
            newIndexWriter.close()
        except OSError as e:
            if not nothrow:
                raise

            print( f'Error closing {newIndexWriter.filePath}: {e}', file = stderr )

    def __cleanup( self ):
        self.__closeNewIndexWriter( True )

        newIndexFile = self.__newIndexFilePath
        if newIndexFile is not None:
            self.__newIndexFilePath = None
            try:
                newIndexFile.unlink()
            except OSError as e:
                print( f'Error removing {newIndexFile}: {e}', file = stderr )
