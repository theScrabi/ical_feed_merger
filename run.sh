#!/usr/bin/bash

source .env

podman run -d \
	--name=calendar_merger \
	--restart always \
	-e GLOBAL_IFM_HOST=$GLOBAL_IFM_HOST \
	-p 5000:5000 \
	calendar_merger
