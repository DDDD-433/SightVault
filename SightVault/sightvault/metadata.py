from datetime import datetime, timedelta, timezone
from PIL import ExifTags

def iso_timestamp(seconds):
    # Windows' C runtime rejects negative POSIX timestamps. UTC arithmetic keeps
    # genuine pre-1970 file dates portable without clamping or inventing a date.
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')

def image_metadata(path, image):
    stat = path.stat()
    timestamp, source = iso_timestamp(stat.st_mtime), 'File modified time'
    try:
        exif = image.getexif()
        details = exif.get_ifd(ExifTags.IFD.Exif)
        original = details.get(36867) or exif.get(36867)
        offset = details.get(36881) or exif.get(36881)
        exif_source = 'EXIF capture time (with timezone)'
        if not original:
            original = exif.get(306)
            offset = details.get(36880) or exif.get(36880)
            exif_source = 'EXIF image time (with timezone)'
        # Do not silently invent a timezone for an EXIF date with no UTC offset.
        if original and offset:
            timestamp = datetime.strptime(str(original) + str(offset), '%Y:%m:%d %H:%M:%S%z').astimezone(timezone.utc).isoformat(timespec='seconds')
            source = exif_source
    except (ValueError, TypeError, AttributeError, OverflowError, OSError, SyntaxError):
        pass
    return {'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
        'created_at':iso_timestamp(getattr(stat, 'st_birthtime', stat.st_ctime)),
        'modified_at':iso_timestamp(stat.st_mtime), 'timestamp':timestamp,
        'timestamp_source':source, 'width':image.width, 'height':image.height}

