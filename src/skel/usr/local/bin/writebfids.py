#!/usr/bin/env python

import os
import sys
import time
import errno
import signal
from zipfile import ZipFile, BadZipfile
from pymongo import MongoClient, errors
import ConfigParser as parser
import logging

running = True

def sigint_handler(signum, frame):
    global running
    print("Caught signal %d." % signum)
    running = False

def main(configfile = '/etc/dcache/container.conf'):
    global running
    logging.basicConfig(filename = '/var/log/dcache/writebfids.log',
                        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s')

    while running:
        try:
            configuration = parser.RawConfigParser(defaults = { 'mongoUri': 'mongodb://localhost/', 'mongoDb': 'smallfiles', 'loopDelay': 5, 'logLevel': 'ERROR' })
            configuration.read(configfile)

            global archiveUser
            global archiveMode
            global mountPoint
            global dataRoot
            global mongoUri
            global mongoDb
            archiveUser = configuration.get('DEFAULT', 'archiveUser')
            archiveMode = configuration.get('DEFAULT', 'archiveMode')
            mountPoint = configuration.get('DEFAULT', 'mountPoint')
            dataRoot = configuration.get('DEFAULT', 'dataRoot')
            mongoUri = configuration.get('DEFAULT', 'mongoUri')
            mongoDb  = configuration.get('DEFAULT', 'mongodb')
            logLevelStr = configuration.get('DEFAULT', 'logLevel')
            logLevel = getattr(logging, logLevelStr.upper(), None)

            loopDelay = configuration.getint('DEFAULT', 'loopDelay')

            logging.getLogger().setLevel(logLevel)

            logging.info('Successfully read configuration from file %s.' % configfile)

            client = MongoClient(mongoUri)
            db = client[mongoDb]
            logging.info("Established db connection")

            with db.archives.find() as archives:
                for archive in archives:
                    if not running:
                        logging.info("Exiting.")
                        sys.exit(1)
                    try:
                        localpath = archive['path'].replace(dataRoot, mountPoint)
                        archivePnfsid = archive['pnfsid']
                        zf = ZipFile(localpath, mode='r', allowZip64 = True)
                        for f in zf.filelist:
                            logging.debug("Entering bfid into record for file %s" % f.filename)
                            filerecord = db.files.find_one( { 'pnfsid': f.filename, 'state': 'archived: %s' % archive['path'] }, await_data=True )
                            if filerecord:
                                url = "dcache://dcache/?store=%s&group=%s&bfid=%s:%s" % (filerecord['store'], filerecord['group'], f.filename, archivePnfsid)
                                filerecord['archiveUrl'] = url
                                filerecord['state'] = 'verified: %s' % archive['path']
                                db.files.save(filerecord)
                                logging.debug("Updated record with URL %s in archive %s" % (url,archive['path']))
                            else:
                                logging.warn("File %s in archive %s has no entry in DB. This could be caused by a previous forced interrupt. Creating failure entry." % (f.filename, archive['path']) )
                                db.failures.insert( { 'archiveId': archivePnfsid, 'pnfsid': f.filename } )

                        logging.debug("stat(%s): %s" % (localpath, os.stat(localpath)))

                        db.archives.remove( { 'pnfsid': archive['pnfsid'] } )
                        logging.debug("Removed entry for archive %s[%s]" % ( archive['path'], archive['pnfsid'] ) )

                    except BadZipfile as e:
                        logging.warn("Archive %s is not yet ready. Will try later." % localpath)

                    except IOError as e:
                        if e.errno != errno.EINTR:
                            logging.error("IOError: %s" % e.strerror)
                        else:
                            logging.info("User interrupt.")

                    except Exception as e:
                        logging.error("Unexpected error: %s" % e.message)

            logging.info("Processed all archive entries. Sleeping 60 seconds.")
            time.sleep(60)

        except errors.ConnectionFailure as e:
            print("Connection to DB failed: %s" % e.message)
            logging.error("Connection to DB failed: %s" %s e.message)
        except parser.NoOptionError as e:
            print("Missing option: %s" % e.message)
            logging.error("Missing option: %s" % e.message)
        except parser.Error as e:
            print("Error reading configfile %s: %s" % (configfile, e.message))
            logging.error("Error reading configfile %s: %s" % (configfile, e.message))
            sys.exit(2)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, sigint_handler)
    if not os.getuid() == 0:
        print("writebfsids.py must run as root!")
        sys.exit(2)

    if len(sys.argv) == 1:
        main()
    elif len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        print("Usage: writebfids.py <configfile>")
        sys.exit(2)

