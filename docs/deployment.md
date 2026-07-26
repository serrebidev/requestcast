# Server deployment

RequestCast runs on a server as an ordinary WSGI application. Configure it entirely through
environment variables — anything set in the environment overrides the settings file, so the
first-run setup page never appears and never needs to be reachable.

## Install

```bash
adduser --system --group --home /var/lib/requestcast requestcast
git clone https://github.com/serrebidev/requestcast.git /opt/requestcast
python3 -m venv /opt/requestcast/.venv
/opt/requestcast/.venv/bin/pip install -r /opt/requestcast/requirements.txt gunicorn
apt install ffmpeg
/opt/requestcast/.venv/bin/pip install yt-dlp
```

## Generate the secrets

Never reuse a key from anywhere else, and keep the file out of version control.

```bash
python3 - <<'EOF'
import hashlib, os, secrets
password = input("Portal password: ")
salt = os.urandom(32)
digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1,
                        dklen=32, maxmem=64 * 1024 * 1024)
print("REQUESTCAST_SECRET_KEY=" + secrets.token_urlsafe(48))
print("REQUESTCAST_PASSWORD_SALT=" + salt.hex())
print("REQUESTCAST_PASSWORD_HASH=" + digest.hex())
EOF
```

Write the output to `/etc/requestcast.env` and `chmod 600` it. The plain password is never
stored — only the salt and the scrypt digest.

## Environment file

```ini
REQUESTCAST_DOWNLOAD_DIR=/var/lib/requestcast/downloads
REQUESTCAST_STATE_DIR=/var/lib/requestcast/state
REQUESTCAST_SECRET_KEY=...
REQUESTCAST_PASSWORD_SALT=...
REQUESTCAST_PASSWORD_HASH=...

# Optional. Omit every AZURACAST line to run as a plain downloader.
REQUESTCAST_AZURACAST_API_BASE=http://127.0.0.1:12000/api
REQUESTCAST_AZURACAST_API_KEY=...
REQUESTCAST_STATION_ID=1
REQUESTCAST_REQUEST_PLAYLIST_ID=10
REQUESTCAST_UPLOAD_DIR=Requests
REQUESTCAST_MEDIA_DIR=/var/azuracast/media/Requests

# Optional. A Deezer subscriber ARL makes Deezer the default audio source
# (FLAC, then 320 kbps MP3), with YouTube as the fallback. Keep it secret.
REQUESTCAST_DEEZER_ARL=...
```

Setting `REQUESTCAST_AZURACAST_API_KEY` enables the AzuraCast integration.

## systemd unit

Save as `/etc/systemd/system/requestcast.service`:

```ini
[Unit]
Description=RequestCast media portal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=requestcast
Group=requestcast
WorkingDirectory=/opt/requestcast
EnvironmentFile=/etc/requestcast.env
ExecStart=/opt/requestcast/.venv/bin/gunicorn --workers 1 --threads 6 \
          --timeout 300 --bind 127.0.0.1:8797 requestcast.app:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/requestcast
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

**Use one worker.** The download queue is a thread inside the process, and a second worker
would compete for the same jobs.

**The 300 second timeout matters.** Indexing a large PDF happens inside the upload request and
takes about twenty seconds for 7,000 rows; the default 30 second timeout kills it.

```bash
systemctl daemon-reload
systemctl enable --now requestcast
```

## nginx

```nginx
server {
    listen 443 ssl http2;
    server_name requestcast.example.com;

    client_max_body_size 20m;   # uploaded lists can be several megabytes

    location / {
        proxy_pass http://127.0.0.1:8797;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

The application trusts one layer of `X-Forwarded-*` headers, so put exactly one proxy in
front of it.

## Health check

`GET /healthz` returns 200 without requiring a login.

## Upgrading

```bash
cd /opt/requestcast && git pull
/opt/requestcast/.venv/bin/pip install -r requirements.txt
systemctl restart requestcast
```

Jobs that were running when the service stopped are returned to the queue at startup.
