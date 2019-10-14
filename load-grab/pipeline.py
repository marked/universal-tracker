# encoding=utf8
import datetime
from distutils.version import StrictVersion
import hashlib
import os.path
import random
from seesaw.config import realize, NumberConfigValue
from seesaw.externalprocess import ExternalProcess
from seesaw.item import ItemInterpolation, ItemValue
from seesaw.task import SimpleTask, LimitConcurrent
from seesaw.tracker import GetItemFromTracker, PrepareStatsForTracker, \
    UploadWithTracker, SendDoneToTracker
import shutil
import socket
import subprocess
import sys
import time
import string
import re
import random

try:
    import warcio
    from warcio.archiveiterator import ArchiveIterator
    from warcio.warcwriter import WARCWriter
except:
    raise Exception("Please install warc with 'sudo pip install warcio --upgrade'.")

import seesaw
from seesaw.externalprocess import WgetDownload
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.util import find_executable

from tornado import httpclient


# check the seesaw version
if StrictVersion(seesaw.__version__) < StrictVersion('0.8.5'):
    raise Exception('This pipeline needs seesaw version 0.8.5 or higher.')



###########################################################################
# The version number of this pipeline definition.
#
# Update this each time you make a non-cosmetic change.
# It will be added to the WARC files and reported to the tracker.
VERSION = '20190503.01'
USER_AGENT = 'ArchiveTeam'
TRACKER_ID = 'load'
TRACKER_HOST = 'tracker'


###########################################################################
# This section defines project-specific tasks.
#
# Simple tasks (tasks that do not need any concurrency) are based on the
# SimpleTask class and have a process(item) method that is called for
# each item.
class CheckIP(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'CheckIP')
        self._counter = 0

    def process(self, item):
        # NEW for 2014! Check if we are behind firewall/proxy

        if self._counter <= 0:
            item.log_output('Checking IP address.')
            ip_set = set()

            ip_set.add(socket.gethostbyname('twitter.com'))
            ip_set.add(socket.gethostbyname('facebook.com'))
            ip_set.add(socket.gethostbyname('youtube.com'))
            ip_set.add(socket.gethostbyname('microsoft.com'))
            ip_set.add(socket.gethostbyname('icanhas.cheezburger.com'))
            ip_set.add(socket.gethostbyname('archiveteam.org'))

            if len(ip_set) != 6:
                item.log_output('Got IP addresses: {0}'.format(ip_set))
                item.log_output(
                    'Are you behind a firewall/proxy? That is a big no-no!')
                raise Exception(
                    'Are you behind a firewall/proxy? That is a big no-no!')

        # Check only occasionally
        if self._counter <= 0:
            self._counter = 10
        else:
            self._counter -= 1


class PrepareDirectories(SimpleTask):
    def __init__(self, warc_prefix):
        SimpleTask.__init__(self, 'PrepareDirectories')
        self.warc_prefix = warc_prefix

    def process(self, item):
        item_name = item['item_name']
        escaped_item_name = item_name.replace(':', '_').replace('/', '_').replace('~', '_')
        item_hash = hashlib.sha1(item_name.encode('utf-8')).hexdigest()
        dirname = '/'.join((item['data_dir'], item_hash))

        if os.path.isdir(dirname):
            shutil.rmtree(dirname)

        os.makedirs(dirname)

        item['item_dir'] = dirname
        item['warc_file_base'] = '%s-%s-%s' % (self.warc_prefix, item_hash,
            time.strftime('%Y%m%d-%H%M%S'))

        open('%(item_dir)s/%(warc_file_base)s.warc.gz' % item, 'w').close()
        open('%(item_dir)s/%(warc_file_base)s_data.txt' % item, 'w').close()


class Deduplicate(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'Deduplicate')

    def process(self, item):
        digests = {}
        input_filename = '%(item_dir)s/%(warc_file_base)s.warc.gz' % item
        output_filename = '%(item_dir)s/%(warc_file_base)s-deduplicated.warc.gz' % item
        with open(input_filename, 'rb') as f_in, \
                open(output_filename, 'wb') as f_out:
            writer = WARCWriter(filebuf=f_out, gzip=True)
            for record in ArchiveIterator(f_in):
                url = record.rec_headers.get_header('WARC-Target-URI')
                if url is not None and url.startswith('<'):
                    url = re.search('^<(.+)>$', url).group(1)
                    record.rec_headers.replace_header('WARC-Target-URI', url)
                if record.rec_headers.get_header('WARC-Type') == 'response':
                    digest = record.rec_headers.get_header('WARC-Payload-Digest')
                    if digest in digests:
                        writer.write_record(
                            self._record_response_to_revisit(writer, record,
                                                             digests[digest])
                        )
                    else:
                        digests[digest] = (
                            record.rec_headers.get_header('WARC-Record-ID'),
                            record.rec_headers.get_header('WARC-Date'),
                            record.rec_headers.get_header('WARC-Target-URI')
                        )
                        writer.write_record(record)
                elif record.rec_headers.get_header('WARC-Type') == 'warcinfo':
                    record.rec_headers.replace_header('WARC-Filename', output_filename)
                    writer.write_record(record)
                else:
                    writer.write_record(record)

    def _record_response_to_revisit(self, writer, record, duplicate):
        warc_headers = record.rec_headers
        warc_headers.replace_header('WARC-Refers-To', duplicate[0])
        warc_headers.replace_header('WARC-Refers-To-Date', duplicate[1])
        warc_headers.replace_header('WARC-Refers-To-Target-URI', duplicate[2])
        warc_headers.replace_header('WARC-Type', 'revisit')
        warc_headers.replace_header('WARC-Truncated', 'length')
        warc_headers.replace_header('WARC-Profile',
                                    'http://netpreserve.org/warc/1.0/' \
                                    'revisit/identical-payload-digest')
        warc_headers.remove_header('WARC-Block-Digest')
        warc_headers.remove_header('Content-Length')
        return writer.create_warc_record(
            record.rec_headers.get_header('WARC-Target-URI'),
            'revisit',
            warc_headers=warc_headers,
            http_headers=record.http_headers
        )


class MoveFiles(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'MoveFiles')

    def process(self, item):
        if os.path.exists('%(item_dir)s/%(warc_file_base)s.warc' % item):
            raise Exception('Please compile wget with zlib support!')

        #os.rename('%(item_dir)s/%(warc_file_base)s-deduplicated.warc.gz' % item,
        #    '%(data_dir)s/%(warc_file_base)s-deduplicated.warc.gz' % item)
        os.rename('%(item_dir)s/%(warc_file_base)s.warc.gz' % item,
            '%(data_dir)s/%(warc_file_base)s.warc.gz' % item)
        os.rename('%(item_dir)s/%(warc_file_base)s_data.txt' % item,
            '%(data_dir)s/%(warc_file_base)s_data.txt' % item)

        shutil.rmtree('%(item_dir)s' % item)


def get_hash(filename):
    with open(filename, 'rb') as in_file:
        return hashlib.sha1(in_file.read()).hexdigest()


CWD = os.getcwd()
PIPELINE_SHA1 = get_hash(os.path.join(CWD, 'pipeline.py'))
LUA_SHA1 = 0


def stats_id_function(item):
    # NEW for 2014! Some accountability hashes and stats.
    d = {
        'pipeline_hash': PIPELINE_SHA1,
        'lua_hash': LUA_SHA1,
        'python_version': sys.version,
    }
    return d



###########################################################################
# Initialize the project.
#
# This will be shown in the warrior management panel. The logo should not
# be too big. The deadline is optional.
project = Project(
    title='load',
    project_html='''
        <img class="project-logo" alt="Project logo" src="https://www.archiveteam.org/images/b/b5/Reddit_logo.png" height="50px" title=""/>
        <h2>reddit.com <span class="links"><a href="https://reddit.com/">Website</a> &middot; <a href="http://tracker.archiveteam.org/reddit/">Leaderboard</a></span></h2>
        <p>Archiving everything from reddit.</p>
    '''
)

pipeline = Pipeline(
    GetItemFromTracker('http://%s/%s' % (TRACKER_HOST, TRACKER_ID), downloader,
        VERSION),
    PrepareStatsForTracker(
        defaults={'downloader': downloader, 'version': VERSION},
        file_groups={
            'data': [
                '/dev/null'
            ]
        },
        id_function=stats_id_function,
    ),
    SendDoneToTracker(
        tracker_url='http://%s/%s' % (TRACKER_HOST, TRACKER_ID),
        stats=ItemValue('stats')
    )
)

