# -*- coding: utf-8 -*-
import argparse
import pathlib
import shutil
import stat
from typing import Callable, Set

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
    findParser.add_argument( '--db', required = True, action = 'append', help = 'photo database' )
    findActionGroup = findParser.add_mutually_exclusive_group()
    findParser.set_defaults( findAction = None )
    findActionGroup.add_argument( '--print', help = 'print files and storage location',
                                  action = 'store_const', dest = 'findAction', const = printFindAction )
    findActionGroup.add_argument( '--print-new', help = 'print only new files',
                                  action = 'store_const', dest = 'findAction', const = printNewFindAction )
    findActionGroup.add_argument( '--move-found-to', help = 'move found files to folder',
                                  dest = 'moveFoundTarget', default = None )
    findActionGroup.add_argument( '--copy-new-to', help = 'copy new files to folder',
                                  dest = 'copyNewTarget', default = None )
    findParser.add_argument( 'FILES', nargs = argparse.REMAINDER,
                             help = 'files or folders to find' )


def findCmdMain( cmdArgs ):
    db = FileDb.FileDb()
    for dbPath in cmdArgs.db:
        db.addIndexedTree( pathlib.Path( dbPath ) )

    action = cmdArgs.findAction
    if action is None:
        if cmdArgs.moveFoundTarget is not None:
            action = MoveFoundFindAction( pathlib.Path( cmdArgs.moveFoundTarget ) )
        elif cmdArgs.copyNewTarget is not None:
            action = CopyNewFindAction( pathlib.Path( cmdArgs.copyNewTarget ) )
        else:
            action = printFindAction

    cmd = FindCommand( action = action, db = db,
                       fileTreeIterator = createFileTreeIterator( cmdArgs ) )
    for filePath in cmdArgs.FILES:
        cmd.process( pathlib.Path( filePath ) )


class FindCommand:
    def __init__( self, *, action: FindActionType, db: FileDb.FileDb, fileTreeIterator: FileDb.FileTreeIterator ):
        self.__db = db
        self.__action = action
        self.__fileTreeIterator = fileTreeIterator

    def process( self, filePath: pathlib.Path ):
        s = filePath.stat()
        if not stat.S_ISDIR( s.st_mode ):
            self.processFile( filePath.parent, filePath )
        else:
            for relativePath in self.__fileTreeIterator.iterate( filePath, sortFolders = True ):
                self.processFile( filePath, relativePath )

    def processFile( self, basePath: pathlib.Path, filePath: pathlib.Path ):
        self.__action( basePath, filePath, self.__db.findFile( basePath.joinpath( filePath ) ) )


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
    indexParser.set_defaults( indexAction = verifyIndexAction )
    indexActionGroup.add_argument( '--create', help = 'create SHA2 file checksum index',
                                   action = 'store_const', dest = 'indexAction', const = createIndexAction )
    indexActionGroup.add_argument( '--verify', help = 'create file checksum index',
                                   action = 'store_const', dest = 'indexAction', const = verifyIndexAction )
    indexParser.add_argument( '--checksum-file', help = 'checksum file',
                              dest = 'checksumFile', default = 'Checksums.sha2' )
    indexParser.add_argument( 'FOLDERS', nargs = argparse.REMAINDER, help = 'folders to index' )


def indexCmdMain( cmdArgs ):
    action = cmdArgs.indexAction

    fileTreeIterator = createFileTreeIterator( cmdArgs )

    checksumFile = pathlib.Path( cmdArgs.checksumFile )
    if len( cmdArgs.FOLDERS ) > 1 and checksumFile.is_absolute():
        raise ValueError( 'absolute path to checksum file is given and multiple folders specified' )

    ok = True
    for folder in cmdArgs.FOLDERS:
        if not action( checksumFile, pathlib.Path( folder ), fileTreeIterator ):
            ok = False

    return 0 if ok else 1


def createIndexAction( checksumFile: pathlib.Path, folder: pathlib.Path, fileTreeIterator: FileDb.FileTreeIterator ):
    with FileDb.ChecksumFileWriter( folder.joinpath( checksumFile ) ) as writer:
        for filePath in sorted( fileTreeIterator.iterate( folder ) ):
            writer.write( filePath, FileDb.calculateChecksum( folder.joinpath( filePath ) ) )

        writer.close()

    return True


def verifyIndexAction( checksumFile: pathlib.Path, folder: pathlib.Path, fileTreeIterator: FileDb.FileTreeIterator ):
    fileSet = set()
    for filePath in fileTreeIterator.iterate( folder ):
        fileSet.add( filePath )

    missingCount = 0
    damagedCount = 0
    algorithm = None
    with FileDb.ChecksumFileReader( folder.joinpath( checksumFile ) ) as reader:
        for fp, c in reader:
            if algorithm is None:
                if len( c ) == 40:
                    algorithm = 'sha1'
                else:
                    algorithm = 'sha256'

            filePath = folder.joinpath( fp )
            if fp not in fileSet:
                missingCount += 1
                print( filePath.as_posix(), 'missing' )
            else:
                fileSet.remove( fp )
                if FileDb.calculateChecksum( filePath, algorithm ) != c:
                    damagedCount += 1
                    print( filePath, 'damaged' )

    newCount = len( fileSet )
    if newCount > 0:
        for fp in sorted( fileSet ):
            print( folder.joinpath( fp ).as_posix(), 'new' )

    if newCount > 0 or missingCount > 0 or damagedCount > 0:
        print( f'{folder.as_posix()}: damaged = {damagedCount}, missing = {missingCount}, new = {newCount}' )
        return False

    print( f'{folder.as_posix()}: OK' )
    return True


if __name__ == "__main__":
    # execute only if run as a script
    exit( main() or 0 )
