# -*- coding: utf-8 -*-
import argparse
import pathlib
import stat
from os import scandir
from typing import Callable, Set

import FileDb


def main():
    parser = argparse.ArgumentParser( description = 'Photo archive tool' )
    commands = parser.add_subparsers( required = True, help = 'available commands' )

    findParser = commands.add_parser( 'find' )
    findParser.set_defaults( execute = find )
    findParser.add_argument( '--db', required = True, help = 'photo database' )
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

    cmdArgs = parser.parse_args()
    cmdArgs.execute( cmdArgs )


FindActionType = Callable[[pathlib.Path, pathlib.Path, FileDb.FileInfo], None]


def find( cmdArgs ):
    dbPath = pathlib.Path( cmdArgs.db )
    db = FileDb.FileDb()
    db.addIndexedTree( dbPath )

    action = cmdArgs.findAction
    if action is None:
        if cmdArgs.moveFoundTarget is not None:
            action = MoveFoundFindAction( pathlib.Path( cmdArgs.moveFoundTarget ), dbPath )
        elif cmdArgs.copyNewTarget is not None:
            action = CopyNewFindAction( pathlib.Path( cmdArgs.copyNewTarget ) )
        else:
            action = printFindAction

    cmd = FindCommand( action = action, db = db )
    for filePath in cmdArgs.FILES:
        cmd.process( pathlib.Path( filePath ) )


class FindCommand:
    def __init__( self, *, action: FindActionType, db: FileDb.FileDb ):
        self.__db = db
        self.__action = action

    def process( self, filePath: pathlib.Path ):
        s = filePath.stat()
        if not stat.S_ISDIR( s.st_mode ):
            self.processFile( filePath.parent, filePath )
        else:
            self.processDir( filePath, filePath )

    def processDir( self, basePath: pathlib.Path, dirPath: pathlib.Path ):
        for dirEntry in sorted( scandir( dirPath ), key = lambda x: x.name.lower() ):
            filePath = dirPath.joinpath( dirEntry.name )
            if dirEntry.is_dir():
                self.processDir( basePath, filePath )
            else:
                self.processFile( basePath, filePath )

    def processFile( self, basePath: pathlib.Path, filePath: pathlib.Path ):
        self.__action( basePath, filePath, self.__db.findFile( filePath ) )


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

    def __init__( self, target: pathlib.Path, dbPath: pathlib.Path ):
        self.__target = target
        self.__dbPath = dbPath
        self.__dirCache = MkDirCache()

    def __call__( self, basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
        if fileInfo is None:
            return

        targetPath = self.__target.joinpath( fileInfo.filePath.relative_to( self.__dbPath ) )
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
        self.__cache = MkDirCache()

    def __call__( self, basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
        if fileInfo is not None:
            return

        targetPath = self.__target.joinpath( filePath.relative_to( basePath ) )
        print( f"cp {filePath} {targetPath}" )


if __name__ == "__main__":
    # execute only if run as a script
    main()
