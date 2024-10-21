#!/usr/bin/bash

podman run -d \
	--name=calendar_merger \
	--restart always \
	-p 5000:5000 \
	calendar_merger
