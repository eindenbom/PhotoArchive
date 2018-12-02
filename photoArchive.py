# -*- coding: utf-8 -*-
import argparse
import pathlib
import shutil
import stat
from sys import stderr
from typing import Callable, Set, Dict, Optional

import FileDb


def main():
    parser = argparse.ArgumentParser( description = 'Photo archive tool' )
    commands = parser.add_subparsers( required = True, help = 'available commands' )

    configureFindCommand( commands.add_parser( 'find' ) )
    configureIndexCommand( commands.add_parser( 'index' ) )

    cmdArgs = parser.parse_args()
    return cmdArgs.execute( cmdArgs )


def createFileTreeIterator( _cmdArgs ):
    iterator = FileDb.FileTreeIterator()
    iterator.addExcluded( '*.sha[12]', 'Thumbs.db' )
    return iterator


FindActionType = Callable[[pathlib.Path, pathlib.Path, FileDb.FileInfo], None]


def configureFindCommand( findParser: argparse.ArgumentParser ):
    findParser.set_defaults( execute = findCmdMain )
    findParser.add_argument( '--db', required = True, action = 'append',
                             type = pathlib.Path, help = 'photo database' )
    findActionGroup = findParser.add_mutually_exclusive_group()
    findParser.set_defaults( findAction = None )
    findActionGroup.add_argument( '--print', help = 'print files and storage location',
                                  action = 'store_const', dest = 'findAction', const = printFindAction )
    findActionGroup.add_argument( '--print-new', help = 'print only new files',
                                  action = 'store_const', dest = 'findAction', const = printNewFindAction )
    findActionGroup.add_argument( '--move-found-to', help = 'move found files to folder',
                                  dest = 'moveFoundTarget', type = pathlib.Path, default = None )
    findActionGroup.add_argument( '--copy-new-to', help = 'copy new files to folder',
                                  dest = 'copyNewTarget', type = pathlib.Path, default = None )
    findParser.add_argument( '--cached-checksums', dest = 'cachedChecksums',
                             type = pathlib.Path, help = 'file with cached checksums' )
    findParser.add_argument( '--cached-checksums-root', dest = 'cachedChecksumsRoot',
                             type = pathlib.Path, help = 'file with cached checksums' )
    findParser.add_argument( 'FILES', nargs = argparse.REMAINDER,
                             type = pathlib.Path, help = 'files or folders to find' )


def findCmdMain( cmdArgs ):
    db = FileDb.FileDb()
    for dbPath in cmdArgs.db:
        db.addIndexedTree( dbPath )

    action = cmdArgs.findAction
    if action is None:
        if cmdArgs.moveFoundTarget is not None:
            action = MoveFoundFindAction( cmdArgs.moveFoundTarget )
        elif cmdArgs.copyNewTarget is not None:
            action = CopyNewFindAction( cmdArgs.copyNewTarget )
        else:
            action = printFindAction

    cmd = FindCommand( action = action, db = db,
                       fileTreeIterator = createFileTreeIterator( cmdArgs ) )

    cachedChecksums = cmdArgs.cachedChecksums
    files = cmdArgs.FILES
    if len( files ) == 0 and cachedChecksums is not None:
        cmd.processChecksumFile( cachedChecksums, cmdArgs.cachedChecksumsRoot )
    else:
        if len( files ) == 0:
            files = [pathlib.Path()]

        if cachedChecksums is not None:
            cmd.addCachedChecksums( cachedChecksums, cmdArgs.cachedChecksumsRoot )

        for filePath in files:
            cmd.process( filePath )


class FindCommand:
    __cachedChecksums: Dict[pathlib.Path, str]

    def __init__( self, *, action: FindActionType, db: FileDb.FileDb, fileTreeIterator: FileDb.FileTreeIterator ):
        self.__db = db
        self.__action = action
        self.__fileTreeIterator = fileTreeIterator
        self.__cachedChecksums = dict()

    def addCachedChecksums( self, checksumFile: pathlib.Path, filterPath: Optional[pathlib.Path] ):
        with FileDb.ChecksumFileReader( checksumFile ) as reader:
            for fp, c in reader:
                if filterPath is not None:
                    try:
                        fp = fp.relative_to( filterPath )
                    except ValueError:
                        continue

                self.__cachedChecksums[fp] = c

    def process( self, filePath: pathlib.Path ):
        s = filePath.stat()
        if not stat.S_ISDIR( s.st_mode ):
            self.processFile( filePath.parent, filePath )
        else:
            for relativePath in self.__fileTreeIterator.iterate( filePath, sortFolders = True ):
                self.processFile( filePath, relativePath )

    def processFile( self, basePath: pathlib.Path, filePath: pathlib.Path ):
        self.__action( basePath, filePath, self.__findFile( basePath, filePath ) )

    def processChecksumFile( self, cachedChecksums: pathlib.Path, filterPath: Optional[pathlib.Path] ):
        with FileDb.ChecksumFileReader( cachedChecksums ) as reader:
            basePath = pathlib.Path()
            for fp, c in reader:
                if filterPath is not None:
                    try:
                        fp = fp.relative_to( filterPath )
                    except ValueError:
                        continue

                self.__action( basePath, fp, self.__findFileByChecksum( fp, c ) )

    def __findFile( self, basePath: pathlib.Path, filePath: pathlib.Path ):
        cachedChecksum = self.__cachedChecksums.get( filePath, None )
        if cachedChecksum is None:
            return self.__db.findFile( basePath.joinpath( filePath ) )
        else:
            return self.__findFileByChecksum( filePath, cachedChecksum )

    def __findFileByChecksum( self, filePath: pathlib.Path, checksum: str ):
        fileInfo = self.__db.get( checksum )
        if fileInfo is not None:
            fileInfo = fileInfo.findBestMatch( filePath )
        return fileInfo


def printFindAction( _basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
    if fileInfo is None:
        foundName = '-'
    else:
        foundName = fileInfo.filePath

    print( f"{filePath} {foundName}" )


def printNewFindAction( _basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
    if fileInfo is not None and fileInfo.filePath.name.lower() != filePath.name.lower():
        print( f"{filePath} {fileInfo.filePath}" )
    if fileInfo is None:
        print( filePath )


class MkDirCache:
    __cache: Set[pathlib.Path]

    def __init__( self ):
        self.__cache = set()

    def mkdir( self, path: pathlib.Path ):
        if path in self.__cache:
            return

        path.mkdir( parents = True, exist_ok = True )
        self.__cache.add( path )


class MoveFoundFindAction:
    __sourceDirs: Set[pathlib.Path]

    def __init__( self, target: pathlib.Path ):
        self.__target = target
        self.__dirCache = MkDirCache()

    def __call__( self, basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
        if fileInfo is None:
            return

        targetPath = self.__target.joinpath( fileInfo.filePath )
        if filePath.name.lower() != targetPath.name.lower():
            print( f"skipping {filePath}, target name mismatch ({targetPath.name})" )
            return

        self.__dirCache.mkdir( targetPath.parent )
        if targetPath.exists():
            print( f"skipping {filePath}, target ({targetPath.name}) already exists" )
            return

        filePath.rename( targetPath )


class CopyNewFindAction:
    def __init__( self, target: pathlib.Path ):
        self.__target = target
        self.__dirCache = MkDirCache()

    def __call__( self, basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
        if fileInfo is not None:
            return

        targetPath = self.__target.joinpath( filePath.relative_to( basePath ) )

        self.__dirCache.mkdir( targetPath.parent )
        if targetPath.exists():
            print( f"skipping {filePath}, target ({targetPath}) already exists" )
            return

        # noinspection PyTypeChecker
        shutil.copy2( filePath, targetPath )


def configureIndexCommand( indexParser: argparse.ArgumentParser ):
    indexParser.set_defaults( execute = indexCmdMain )
    indexActionGroup = indexParser.add_mutually_exclusive_group()
    indexParser.set_defaults( indexAction = 'verify' )
    indexActionGroup.add_argument( '--create', help = 'create SHA2 file checksum index',
                                   action = 'store_const', dest = 'indexAction', const = 'create' )
    indexActionGroup.add_argument( '--update', help = 'update SHA2 file checksum index',
                                   action = 'store_const', dest = 'indexAction', const = 'update' )
    indexActionGroup.add_argument( '--verify', help = 'verify file checksum index',
                                   action = 'store_const', dest = 'indexAction', const = 'verify' )
    indexParser.add_argument( '--checksum-file', help = 'checksum file',
                              type = pathlib.Path, dest = 'checksumFile', default = None )
    indexParser.add_argument( '--changes-mode', help = 'context changes handling mode',
                              choices = ['reject', 'review', 'accept'], default = 'reject',
                              dest = 'changesMode' )
    indexParser.add_argument( 'FOLDERS', nargs = argparse.REMAINDER,
                              type = pathlib.Path, help = 'folders to index' )


def indexCmdMain( cmdArgs ):
    indexFileName = cmdArgs.checksumFile
    folders = cmdArgs.FOLDERS

    if len( folders ) > 1 and indexFileName.is_absolute():
        raise ValueError( 'absolute path to checksum file is given and multiple folders specified' )

    if len( folders ) == 0:
        folders = [pathlib.Path()]

    fileTreeIterator = createFileTreeIterator( cmdArgs )

    create = cmdArgs.indexAction != 'verify'
    verify = cmdArgs.indexAction != 'create'

    rejectChanges = create and cmdArgs.changesMode == 'reject'
    reviewChanges = create and cmdArgs.changesMode == 'review'

    success = True
    try:
        for folder in folders:
            indexBuilder = FileDb.IndexBuilder( folder = folder, indexFileName = indexFileName,
                                                fileTreeIterator = fileTreeIterator,
                                                create = create, verify = verify,
                                                rejectChanges = rejectChanges, reviewChanges = reviewChanges )

            if not indexBuilder.run():
                success = False

    except FileDb.IndexValidationError as e:
        print( e, file = stderr )
        return 2

    return 0 if success else 1


if __name__ == "__main__":
    # execute only if run as a script
    exit( main() or 0 )
