from datetime import datetime, timezone
from PIL import ExifTags

def iso_timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='seconds')

def image_metadata(path, image):
    stat = path.stat()
    timestamp, source = iso_timestamp(stat.st_mtime), 'File modified time'
    try:
        exif = image.getexif()
        original = exif.get(36867) or exif.get(306)
        offset = exif.get(36881)
        # Do not silently invent a timezone for an EXIF date with no UTC offset.
        if original and offset:
            timestamp = datetime.strptime(str(original) + str(offset), '%Y:%m:%d %H:%M:%S%z').astimezone(timezone.utc).isoformat(timespec='seconds')
            source = 'EXIF capture time (with timezone)'
    except (ValueError, TypeError, AttributeError):
        pass
    return {'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
        'created_at':iso_timestamp(getattr(stat, 'st_birthtime', stat.st_ctime)),
        'modified_at':iso_timestamp(stat.st_mtime), 'timestamp':timestamp,
        'timestamp_source':source, 'width':image.width, 'height':image.height}

