"""ENGINEERING_GLUE: bounded read-only Range sender for sealed native archives.

Only a server-selected path and recording ID enter here. No capture/control,
archive rewrite, generic URL proxy or additional camera/serial owner.
"""
import hashlib
import os
import re
import stat

MAX_ARCHIVE_BYTES=8*1024**3
CHUNK_BYTES=128*1024


def send_native_capture_download(handler,path,recording_id):
    if not isinstance(recording_id,str) or not re.fullmatch('[0-9a-f]{32}',recording_id):
        handler.send_error(400,'Native recording ID required');return
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as source:
        metadata=os.fstat(source.fileno());size=metadata.st_size
        if not stat.S_ISREG(metadata.st_mode) or not 0<size<=MAX_ARCHIVE_BYTES:
            handler.send_error(400,'Bounded sealed archive required');return
        checksum=hashlib.file_digest(source,'sha256').hexdigest();etag='"'+checksum+'"'
        ranges=handler.headers.get_all('Range',[]);conditions=handler.headers.get_all('If-Range',[])
        if len(ranges)>1 or len(conditions)>1:
            handler.send_error(400,'One archive byte range required');return
        offset=0;partial=False
        if ranges and (not conditions or conditions[0]==etag):
            match=re.fullmatch(r'bytes=([0-9]{1,20})-',ranges[0])
            if match is None or int(match[1])>=size:
                handler.send_response(416);handler.send_header('Content-Range','bytes */'+str(size))
                handler.send_header('Content-Length','0');handler.end_headers();return
            offset=int(match[1]);partial=True
        handler.send_response(206 if partial else 200)
        handler.send_header('Content-Type','application/x-tar');handler.send_header('Accept-Ranges','bytes')
        handler.send_header('ETag',etag);handler.send_header('X-Content-SHA256',checksum)
        handler.send_header('Content-Length',str(size-offset))
        handler.send_header('Content-Disposition','attachment; filename="native-capture-'+recording_id+'.tar"')
        if partial:handler.send_header('Content-Range','bytes '+str(offset)+'-'+str(size-1)+'/'+str(size))
        handler.end_headers();source.seek(offset);remaining=size-offset
        while remaining:
            block=source.read(min(CHUNK_BYTES,remaining))
            if not block:raise OSError('Sealed archive truncated during read')
            handler.wfile.write(block);remaining-=len(block)
