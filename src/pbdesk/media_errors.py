"""Fixed image feedback; never display provider bodies, URLs or exception text."""

IMAGE_ERRORS = {
    'source_blocked': 'This image source is blocked by the running core. Restart the core after updating image support.',
    'not_found': 'This image is no longer in cached history. Refresh history and try again.',
    'unavailable': 'The image server denied access or no longer has this image. Refresh history and retry.',
    'too_large': 'This image exceeds the preview size limit.',
    'format': 'The image server returned an unsupported image format.',
    'download_failed': 'The core could not download the image. Check the network and retry.',
    'timeout': 'Loading the image timed out. Try again.',
    'account_changed': 'The account changed. Select the image again.',
    'access_denied': 'The core denied image access. Reconnect the desktop and try again.',
    'core_update': 'Restart the core to enable image previews.',
    'core_unavailable': 'Could not reach the core. Wait for reconnection, then retry.',
    'decode_failed': 'The image response could not be decoded or exceeds the preview limit.',
    'unknown': 'The image could not be loaded. Retry, or restart the core.',
}
