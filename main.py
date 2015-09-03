from git.repo.base import Repo
import os
from os.path import join    
import sys
import zipfile
import shutil
import time
from stat import S_ISDIR
import argparse
import paramiko
import tempfile
from os.path import basename

# PATH = os.getcwd()
PATH = tempfile.mkdtemp()

def sftp_walk(sftp,remotepath):
    # Kindof a stripped down  version of os.walk, implemented for 
    # sftp.  Tried running it flat without the yields, but it really
    # chokes on big directories.
    path=remotepath
    files=[]
    folders=[]
    for f in sftp.listdir_attr(remotepath):
        if S_ISDIR(f.st_mode):
            folders.append(f.filename)
        else:
            files.append(f.filename)
    yield path,folders,files
    for folder in folders:
        new_path=os.path.join(remotepath,folder)
        for x in sftp_walk(sftp, new_path):
            yield x

def zipper(dir, zip_file):
    # zip a complete directory
    zip = zipfile.ZipFile(zip_file, 'w', compression=zipfile.ZIP_DEFLATED)
    root_len = len(os.path.abspath(dir))
    for root, dirs, files in os.walk(dir):
        archive_root = os.path.abspath(root)[root_len:]
        for f in files:
            fullpath = os.path.join(root, f)
            archive_name = os.path.join(archive_root, f)
            zip.write(fullpath, archive_name, zipfile.ZIP_DEFLATED)
    zip.close()
    return zip_file



def remote_file_exists(sftp, remote_file):
    filestat = None
    try:
         filestat = sftp.stat(remote_file)
    except Exception, e:
         pass
    if filestat:
        return True
    return False


def put(sftp, localfile,remotefile):
    #  Copy localfile to remotefile, overwriting or creating as needed.    
    sftp.put(localfile,remotefile)


def put_all(sftp,localpath,remotepath):    
    #  recursively upload a full directory
    os.chdir(os.path.split(localpath)[0])
    parent=os.path.split(localpath)[1]
    
    for walker in os.walk(parent):        
        try:

            sftp.mkdir(os.path.join(remotepath,walker[0]))
        except Exception, e:
            print e
        for file in walker[2]:
            put(sftp, os.path.join(walker[0],file),os.path.join(remotepath,walker[0],file))

def get(sftp,remotefile,localfile):
    #  Copy remotefile to localfile, overwriting or creating as needed.
    sftp.get(remotefile,localfile)


def get_all(sftp,remotepath,localpath):
    #  recursively download a full directory
    #  Harder than it sounded at first, since paramiko won't walk
    #
    # For the record, something like this would gennerally be faster:
    # ssh user@host 'tar -cz /source/folder' | tar -xz

    sftp.chdir(os.path.split(remotepath)[0])
    parent=os.path.split(remotepath)[1]
    try:
        os.mkdir(localpath)
    except:
        pass
    for walker in sftp_walk(sftp, parent):
        try:
            os.mkdir(os.path.join(localpath,walker[0]))
        except:
            pass
        for file in walker[2]:
            get(sftp, os.path.join(walker[0],file),os.path.join(localpath,walker[0],file))


def isdir(sftp, path):
    # check if a remote file is a directory
    try:
        return S_ISDIR(sftp.stat(path).st_mode)
    except IOError:
        return False

def rm(sftp, path):
    # remove a complete remote directory
    files = sftp.listdir(path=path)

    for f in files:
        filepath = os.path.join(path, f)
        if isdir(sftp, filepath):
            rm(sftp,filepath)
        else:
            sftp.remove(filepath)

    sftp.rmdir(path)


if __name__ == '__main__':
    
    parser = argparse.ArgumentParser()
    parser.add_argument("git_repo", help="git repository to clone")
    parser.add_argument("app_path", help="application remote path")
    parser.add_argument("-r","--revert", help="revert to datetime", default=False)
    args = parser.parse_args()
    
    git_repo = args.git_repo
    app_path = args.app_path

    username = None
    password = None
    address = None
    port = None
    app_folder = None

    try:
        credentials, server = app_path.split('@')
        username = credentials
        if len(credentials.split(':')) == 2:
            username, password = credentials.split(':')
        if len(server.split(':')) == 2:
            address, app_folder = server.split(':')
        elif len(server.split(':')) == 3:
            address, port, app_folder = server.split(':')
        else:
            print 'input error'
            sys.exit(1)

        repo_name = git_repo.split('/')[-1]
        repo_name = repo_name if repo_name.endswith('.git') is False else repo_name[0:-4]

    except Exception, e:
        print 'input error'
        print e
        sys.exit(1)
    
    try:
        transport = paramiko.Transport(address)
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception, e:
        print 'Error in ssh connection'
        print e
        sys.exit(3)

    try:

        if args.revert:
            backup_file_name = repo_name+'-'+args.revert+'.zip'
            backup_file_path = os.path.join(app_folder,repo_name+'-'+args.revert+'.zip')
            get(sftp, backup_file_path, os.path.join(PATH,backup_file_name))

            fh = open(os.path.join(PATH, backup_file_name), 'rb')
            z = zipfile.ZipFile(fh)
            for name in z.namelist():
                outpath = os.path.join(PATH, repo_name)
                z.extract(name, outpath)
            fh.close()
            rm(sftp, os.path.join(app_folder,repo_name))
            put_all(sftp, os.path.join(PATH,repo_name), app_folder)

            exit(0)

        try:
            Repo.clone_from(git_repo, os.path.join(PATH, repo_name))
        except Exception, e:
            print 'git error'
            print e
            sys.exit(2)

        if not remote_file_exists(sftp, app_folder+repo_name):
            # sftp.mkdir(app_folder+repo_name)
            put_all(sftp, os.path.join(PATH,repo_name), app_folder)
            shutil.rmtree(os.path.join(PATH,repo_name))
        else:
            os.mkdir(os.path.join(PATH, 'tmp'))
            get_all(sftp,app_folder+repo_name, os.path.join(PATH,'tmp'))

            zip_name = repo_name+'-'+time.strftime("%d-%m-%Y-%I:%M:%S")+'.zip'
            
            zipper(os.path.join(PATH,'tmp',repo_name), os.path.join(PATH,'tmp',zip_name))

            put(sftp, os.path.join(PATH,'tmp',zip_name), os.path.join(app_folder,zip_name))
            # os.remove(os.path.join(PATH,'tmp',zip_name))
            rm(sftp, os.path.join(app_folder,repo_name))
            put_all(sftp, os.path.join(PATH,'tmp',repo_name), app_folder)
            shutil.rmtree(os.path.join(PATH,'tmp'))
            shutil.rmtree(os.path.join(PATH,repo_name))
    except Exception, e:
        print 'Error in file operations'
        print e
        sys.exit(4)
    sys.exit(0)