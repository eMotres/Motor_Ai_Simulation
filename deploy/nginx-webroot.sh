#!/bin/sh
# Web root (2026-10-18): the deploy publishes each build into /srv/web/builds/<sha> on the host and switches the
# symlink /srv/web/current atomically - pages change without restarting this container, and the older builds' hashed
# assets stay available to pages that are already open.  Without the host directory the image's own bundle is served.
if [ -d /srv/web/current ]; then
  echo "root /srv/web/current;" > /etc/nginx/webroot.conf
else
  echo "root /usr/share/nginx/html;" > /etc/nginx/webroot.conf
fi
