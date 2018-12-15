#!/usr/bin/python3
# -*- coding: utf-8 -*-
import argparse
import fnmatch
import pathlib
import re
import shutil
import stat
from sys import stderr
from typing import Callable, Set, Dict, Optional, List, Pattern

import FileDb


def main():
    parser = argparse.ArgumentParser( description = 'Photo archive tool' )
    commands = parser.add_subparsers( help = 'available commands' )
    parser.set_defaults( execute = None )

    configureFindCommand( commands.add_parser( 'find', help = 'lookup file tree in photo database' ) )
    configureIndexCommand( commands.add_parser( 'index', help = 'create or verify photo database index' ) )
    configureCheckDuplicatesCommand( commands.add_parser(
        'check-duplicates', help = 'check that files with identical checksums are identical' ) )
    configureRestoreCommand( commands.add_parser(
        'restore', help = 'restore files in indexed location from another database' ) )

    cmdArgs = parser.parse_args()

    execute = cmdArgs.execute
    if execute is None:
        parser.error( 'No command is given.' )

    return execute( cmdArgs )


def createFileTreeIterator( _cmdArgs ):
    iterator = FileDb.FileTreeIterator()
    iterator.addExcluded( '*.sha[12]', 'Thumbs.db', '@*' )
    return iterator


FindActionType = Callable[[pathlib.Path, pathlib.Path, FileDb.FileInfo], None]


def configureFindCommand( findParser: argparse.ArgumentParser ):
    findParser.set_defaults( execute = findCmdMain )
    findParser.add_argument( '--db', required = True, action = 'append',
                             type = pathlib.Path, help = 'photo database' )
    findActionGroup = findParser.add_mutually_exclusive_group()
    findActionGroup.add_argument( '--print', help = 'print files and storage location',
                                  action = 'store_true' )
    findActionGroup.add_argument( '--move-to', help = 'move found files to folder',
                                  dest = 'moveTarget', type = pathlib.Path, default = None )
    findActionGroup.add_argument( '--copy-to', help = 'copy new files to folder',
                                  dest = 'copyTarget', type = pathlib.Path, default = None )
    findParser.add_argument( '--new', action = 'store_true', help = 'process new files (not found in database)' )
    findParser.add_argument( '--ignore-renames', action = 'store_true',
                             dest = 'ignoreRenames', help = 'do not print renamed files' )
    findParser.add_argument( '--cached-checksums', dest = 'cachedChecksums',
                             type = pathlib.Path, help = 'file with cached checksums' )
    findParser.add_argument( '--cached-checksums-root', dest = 'cachedChecksumsRoot',
                             type = pathlib.Path, help = 'path prefix to remove from cached checksums' )
    findParser.add_argument( '--excluded-list', dest = 'excludedList',
                             type = pathlib.Path, help = 'file with excluded paths and patterns' )
    findParser.add_argument( 'FILES', nargs = argparse.REMAINDER,
                             type = pathlib.Path, help = 'files or folders to find' )


def findCmdMain( cmdArgs ):
    db = FileDb.FileDb()
    for dbPath in cmdArgs.db:
        db.addIndexedTree( dbPath )

    processNew = cmdArgs.new

    if cmdArgs.moveTarget is not None:
        action = CopyFindAction( target = cmdArgs.moveTarget, move = True, new = processNew )
    elif cmdArgs.copyTarget is not None:
        action = CopyFindAction( target = cmdArgs.copyTarget, move = False, new = processNew )
    elif processNew:
        action = printOnlyNewFindAction if cmdArgs.ignoreRenames else printNewFindAction
    else:
        action = printFindAction

    cmd = FindCommand( action = action, db = db,
                       fileTreeIterator = createFileTreeIterator( cmdArgs ) )

    if cmdArgs.excludedList is not None:
        cmd.addExcludedList( cmdArgs.excludedList )

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
    __excludedPatterns: List[Pattern]
    __excludedFiles: Set[pathlib.Path]
    __excludedPaths: Set[pathlib.Path]

    def __init__( self, *, action: FindActionType, db: FileDb.FileDb, fileTreeIterator: FileDb.FileTreeIterator ):
        self.__db = db
        self.__action = action
        self.__fileTreeIterator = fileTreeIterator
        self.__cachedChecksums = dict()
        self.__excludedPatterns = list()
        self.__excludedFiles = set()
        self.__excludedPaths = set()

    def addCachedChecksums( self, checksumFile: pathlib.Path, filterPath: Optional[pathlib.Path] ):
        with FileDb.ChecksumFileReader( checksumFile ) as reader:
            for fp, c in reader:
                if filterPath is not None:
                    try:
                        fp = fp.relative_to( filterPath )
                    except ValueError:
                        continue

                self.__cachedChecksums[fp] = c

    def addExcludedList( self, listFileName ):
        empty = pathlib.Path()
        with open( listFileName, mode = 'rt', encoding = 'utf-8-sig' ) as file:
            while True:
                l = file.readline()
                if len( l ) == 0:
                    break

                l = l.rstrip( '\r\n' )
                if len( l ) == 0:
                    continue

                path = pathlib.Path( l )
                if path == empty:
                    continue

                if (not '/' in l) and (not '\\' in l):
                    # Только имя файла - это исключающий шаблон
                    self.__excludedPatterns.append( re.compile(
                        fnmatch.translate( path.name ), re.RegexFlag.IGNORECASE | re.RegexFlag.DOTALL ) )
                elif l.endswith( '/' ) or l.endswith( '\\' ):
                    # префикс пути
                    self.__excludedPaths.add( path )
                else:
                    # точное имя файла
                    self.__excludedFiles.add( path )

    def process( self, filePath: pathlib.Path ):
        s = filePath.stat()
        if not stat.S_ISDIR( s.st_mode ):
            self.processFile( filePath.parent, filePath )
        else:
            for relativePath in self.__fileTreeIterator.iterate( filePath ):
                self.processFile( filePath, relativePath )

    def isExcluded( self, filePath: pathlib.Path ):
        if filePath in self.__excludedFiles:
            return True

        excludedPaths = self.__excludedPaths
        if len( excludedPaths ) > 0:
            for p in filePath.parents:
                if p in excludedPaths:
                    return True

        name = filePath.name
        for p in self.__excludedPatterns:
            if p.match( name ):
                return True

        return False

    def processFile( self, basePath: pathlib.Path, filePath: pathlib.Path ):
        if not self.isExcluded( filePath ):
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

                if not self.isExcluded( fp ):
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


def printOnlyNewFindAction( _basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
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


class CopyFindAction:
    def __init__( self, *, target: pathlib.Path, move: bool, new: bool ):
        self.__target = target
        self.__move = move
        self.__new = new
        self.__dirCache = MkDirCache()

    def __call__( self, basePath: pathlib.Path, filePath: pathlib.Path, fileInfo: FileDb.FileInfo ):
        if fileInfo is not None:
            if self.__new:
                return

            targetPath = self.__target.joinpath( fileInfo.filePath )
            if filePath.name.lower() != targetPath.name.lower():
                print( f"skipping {filePath}, target name mismatch ({targetPath.name})" )
                return

        else:
            if not self.__new:
                return

            targetPath = self.__target.joinpath( filePath )

        self.__dirCache.mkdir( targetPath.parent )
        if targetPath.exists():
            print( f"skipping {filePath}, target ({targetPath.name}) already exists" )
            return

        sourcePath = basePath.joinpath( filePath )
        if self.__move:
            sourcePath.rename( targetPath )
        else:
            # noinspection PyTypeChecker
            shutil.copy2( sourcePath, targetPath )


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
    indexParser.add_argument( '--reuse-checksums', help = 'do not recalculate checksums for files already in index',
                              action = 'store_true', dest = 'reuseChecksums' )
    indexParser.add_argument( 'FOLDERS', nargs = argparse.REMAINDER,
                              type = pathlib.Path, help = 'folders to index' )


def indexCmdMain( cmdArgs ):
    indexFileName = cmdArgs.checksumFile
    folders = cmdArgs.FOLDERS

    if len( folders ) > 1 and indexFileName is not None and indexFileName.is_absolute():
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
                                                rejectChanges = rejectChanges, reviewChanges = reviewChanges,
                                                reuseChecksums = cmdArgs.reuseChecksums )

            if not indexBuilder.run():
                success = False

    except FileDb.IndexValidationError as e:
        print( e, file = stderr )
        return 2

    return 0 if success else 1


def configureCheckDuplicatesCommand( indexParser: argparse.ArgumentParser ):
    indexParser.set_defaults( execute = checkDuplicatesCmdMain )
    indexParser.add_argument( '--storage-base', help = 'base path of indexed file storage',
                              type = pathlib.Path, dest = 'storageBase', default = None )
    indexParser.add_argument( 'FOLDERS', nargs = argparse.REMAINDER,
                              type = pathlib.Path, help = 'photo database root folders' )


def checkDuplicatesCmdMain( cmdArgs ):
    folders = cmdArgs.FOLDERS
    if len( folders ) == 0:
        folders = [pathlib.Path()]

    db = FileDb.FileDb()
    for dbPath in folders:
        db.addIndexedTree( pathlib.Path(), dbPath )

    storageBase = cmdArgs.storageBase

    duplicates = list()
    for _, fileInfo in db.entries():
        if fileInfo.duplicate is not None:
            duplicates.append( fileInfo )

    success = True
    for fileInfo in sorted( duplicates, key = lambda x: x.id ):
        duplicate = fileInfo.duplicate
        while duplicate is not None:
            if not checkDuplicates( storageBase, fileInfo, duplicate ):
                success = False
                print( f"'{fileInfo.filePath}' and '{duplicate.filePath}' are binary different" )
            duplicate = duplicate.duplicate

    return 0 if success else 1


def checkDuplicates( storageBase: Optional[pathlib.Path], fileInfo: FileDb.FileInfo, duplicate: FileDb.FileInfo ):
    srcPath = fileInfo.filePath
    dstPath = duplicate.filePath
    if storageBase is not None:
        srcPath = storageBase.joinpath( srcPath )
        dstPath = storageBase.joinpath( dstPath )

    chunkSize = 0x10000
    with srcPath.open( mode = 'rb' ) as src:
        with dstPath.open( mode = 'rb' ) as dst:
            while True:
                sd = src.read( chunkSize )
                dd = dst.read( chunkSize )
                if sd != dd:
                    return False

                if len( sd ) == 0:
                    break

    return True


def configureRestoreCommand( restoreParser: argparse.ArgumentParser ):
    restoreParser.set_defaults( execute = restoreCmdMain )
    restoreParser.add_argument( '--db', required = True, action = 'append',
                                type = pathlib.Path, help = 'photo database' )
    restoreParser.add_argument( '--db-storage', help = 'path to storage of indexed files',
                                type = pathlib.Path, dest = 'dbStorage', default = None )
    restoreParser.add_argument( '--checksum-file', help = 'checksum file',
                                type = pathlib.Path, dest = 'checksumFile', default = None )
    restoreParser.add_argument( '--skip-existing', help = 'skip files already in restored folder',
                                dest = 'skipExisting', action = 'store_true' )
    restoreParser.add_argument( 'FOLDERS', nargs = argparse.REMAINDER,
                                type = pathlib.Path, help = 'folders to restore from database' )


def restoreCmdMain( cmdArgs ):
    db = FileDb.FileDb()
    for dbPath in cmdArgs.db:
        db.addIndexedTree( pathlib.Path(), dbPath )

    restoreCmd = RestoreCommand( db = db,
                                 dbStorage = cmdArgs.dbStorage,
                                 checksumFile = cmdArgs.checksumFile,
                                 skipExisting = cmdArgs.skipExisting )

    folders = cmdArgs.FOLDERS
    if len( folders ) == 0:
        folders = [pathlib.Path()]

    success = restoreCmd.process( folders )

    return 0 if success else 1


class RestoreCommand:
    def __init__( self, *, db: FileDb.FileDb, dbStorage = Optional[pathlib.Path],
                  checksumFile: Optional[pathlib.Path], skipExisting: bool ):
        self.__db = db

        if dbStorage is None:
            dbStorage = pathlib.Path()
        self.__dbStorage = dbStorage

        if checksumFile is None:
            checksumFile = pathlib.Path( 'Checksums.sha2' )
        self.__checksumFile = checksumFile

        self.__skipExisting = skipExisting
        self.__mkdirCache = MkDirCache()

    def process( self, folders: List[pathlib.Path] ):
        success = True
        for folder in folders:
            with FileDb.ChecksumFileReader( folder.joinpath( self.__checksumFile ) ) as reader:
                for filePath, cs in reader:
                    if not self.__restoreFile( cs, folder, filePath ):
                        success = False

        return success

    def __restoreFile( self, cs: str, basePath: pathlib.Path, filePath: pathlib.Path ):
        fileInfo = self.__db.get( cs )
        if fileInfo is None:
            print( f"'{filePath}' is not found in database", file = stderr )
            return False

        fileInfo = fileInfo.findBestMatch( filePath )

        fullPath = basePath.joinpath( filePath )
        self.__mkdirCache.mkdir( fullPath.parent )

        if fullPath.exists():
            if self.__skipExisting:
                return True
            else:
                print( f"'{filePath}' already exists", file = stderr )
                return False

        srcPath = fileInfo.filePath
        if self.__dbStorage is not None:
            srcPath = self.__dbStorage.joinpath( srcPath )

        # noinspection PyTypeChecker
        shutil.copy2( srcPath, fullPath )


if __name__ == "__main__":
    # execute only if run as a script
    exit( main() or 0 )
