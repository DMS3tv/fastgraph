from pathlib import Path

import paramiko


def upload_export_sftp(
    local_path: Path,
    host: str,
    port: int,
    username: str,
    password: str,
) -> None:
    """
    Upload exported file to Squiglink endpoint over SFTP.
    Remote destination path is intentionally server-controlled by login context;
    we upload to the authenticated account's default location using the filename.
    """
    transport = paramiko.Transport((host, int(port)))
    try:
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            sftp.put(str(local_path), local_path.name)
        finally:
            sftp.close()
    finally:
        transport.close()
