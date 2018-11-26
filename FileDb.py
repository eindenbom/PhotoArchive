# -*- coding: utf-8 -*-
import fnmatch
import hashlib
import pathlib
import re
from collections import deque
from os import scandir
from typing import Dict, List, Optional, Pattern, Union


class FileInfo:
    __slots__ = ["__filePath", "__checksum", "__duplicate"]

    __filePath: pathlib.Path
    __checksum: str
    __duplicate: "FileInfo"

    def __init__( self, filePath: pathlib.Path, checksum: str ):
        self.__filePath = filePath
        self.__checksum = checksum
        self.__duplicate = None

    @property
    def filePath( self ):
        return self.__filePath

    @property
    def checksum( self ):
        return self.__checksum

    @property
    def duplicate( self ):
        return self.__duplicate

    @duplicate.setter
    def duplicate( self, other: "FileInfo" ):
        self.__duplicate = other


class FileDb:
    __hashIndex: Dict[str, FileInfo]
    __hasSHA1: bool
    __hasSHA256: bool

    def __init__( self ):
        self.__hashIndex = { }
        self.__hasSHA1 = False
        self.__hasSHA256 = False

    @property
    def hasSHA1( self ):
        return self.__hasSHA1

    @property
    def hasSHA256( self ):
        return self.__hasSHA256

    def addFile( self, filePath: pathlib.Path, checksum: str ):
        if len( checksum ) == 64:
            self.__hasSHA256 = True
        elif len( checksum ) == 40:
            self.__hasSHA1 = True
        else:
            raise ValueError( 'Unknown checksum type' )

        fileInfo = FileInfo( filePath, checksum )
        prevInfo = self.__hashIndex.get( checksum, None )
        if prevInfo is None:
            self.__hashIndex[checksum] = fileInfo
        else:
            while prevInfo.duplicate is not None:
                prevInfo = prevInfo.duplicate

            prevInfo.duplicate = fileInfo

    def addChecksumFile( self, basePath: Optional[pathlib.Path], fileName: pathlib.Path ):
        with ChecksumFileReader( fileName ) as reader:
            for fp, c in reader:
                if basePath is not None:
                    fp = basePath.joinpath( fp )

                self.addFile( fp, c )

    def addIndexedTree( self, basePath: pathlib.Path ):
        for indexFileName in ('Checksums.sha2', 'Checksums.sha1'):
            indexFilePath = basePath.joinpath( indexFileName )
            if indexFilePath.exists():
                self.addChecksumFile( basePath, indexFilePath )
                return

        with scandir( basePath ) as it:
            for fileEntry in it:
                if fileEntry.is_dir():
                    self.addIndexedTree( basePath.joinpath( fileEntry.name ) )

    def get( self, checksum: str ):
        return self.__hashIndex.get( checksum, None )

    def findFile( self, filePath: pathlib.Path ):
        fileInfo = self.__findFileInfoChain( filePath )

        if fileInfo is not None and fileInfo.duplicate is not None:
            # есть файлы с одинаковой чек-суммой, ищем первый совпадающий по имени
            fileName = filePath.name.lower()

            f = fileInfo
            while f is not None:
                if f.filePath.name.lower() == fileName:
                    return f

                f = f.duplicate

        return fileInfo

    def __findFileInfoChain( self, filePath: pathlib.Path ):
        if self.hasSHA256:
            fileInfo = self.get( calculateChecksum( filePath, 'sha256' ) )
            if fileInfo is not None:
                return fileInfo

        if self.hasSHA1:
            fileInfo = self.get( calculateChecksum( filePath, 'sha1' ) )
            if fileInfo is not None:
                return fileInfo

        return None


def calculateChecksum( filePath: pathlib.Path, algorithm: str = 'sha256' ):
    h = hashlib.new( algorithm )
    with filePath.open( mode = 'rb', buffering = False ) as file:
        while True:
            data = file.read( 0x10000 )
            if len( data ) == 0:
                break

            h.update( data )

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
        subdirQueue = deque()
        subdir = pathlib.Path()
        while True:
            for dirEntry in scandir( basePath.joinpath( subdir ) ):
                filePath = subdir.joinpath( dirEntry.name )
                if dirEntry.is_dir():
                    subdirQueue.append( filePath )
                elif self.__checkName( dirEntry.name ):
                    yield filePath

            if len( subdirQueue ) == 0:
                break

            subdir = subdirQueue.popleft()

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
            l = self.__file.readline()
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
