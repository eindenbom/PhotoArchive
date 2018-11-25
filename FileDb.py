# -*- coding: utf-8 -*-
import hashlib
import pathlib
from os import scandir
from typing import Dict, Optional


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
        with fileName.open( mode = 'rt', encoding = 'utf-8' ) as file:
            no = 0
            while True:
                l = file.readline()
                if l == '':
                    break

                no += 1
                l = l.rstrip( '\r\n' )
                if l == '':
                    continue

                c, s, n = l.partition( ' ' )
                if n != '' and (n[0] == '*' or n[0] == ' '):
                    n = n[1:]

                if s == '' or n == '':
                    raise ValueError( 'Invalid checksum line #{no} in {fileName}' )

                fp = pathlib.Path( n )
                if basePath is not None:
                    fp = basePath.joinpath( fp )

                self.addFile( fp, c )

            file.close()

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
            fileInfo = self.get( self.calculateChecksum( filePath, 'sha256' ) )
            if fileInfo is not None:
                return fileInfo

        if self.hasSHA1:
            fileInfo = self.get( self.calculateChecksum( filePath, 'sha1' ) )
            if fileInfo is not None:
                return fileInfo

        return None

    @staticmethod
    def calculateChecksum( filePath: pathlib.Path, algorithm: str ):
        h = hashlib.new( algorithm )
        with filePath.open( mode = 'rb', buffering = False ) as file:
            while True:
                data = file.read( 0x10000 )
                if len( data ) == 0:
                    break

                h.update( data )

            file.close()

        return h.hexdigest()
