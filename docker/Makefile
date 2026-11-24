# MR2 Docker: run from this folder (docker/). See README.md.
#   make local            whole system in simulation (https://localhost)
#   make local-viewer     + RViz/Gazebo GUI at http://localhost:6080
#   make down             stop everything
DOCKER := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO   := $(abspath $(DOCKER)/..)
VIDEO ?= false
export MR2_VIDEO := $(VIDEO)
COMPOSE := docker compose -f $(DOCKER)/compose/local/compose.local.yaml
EVIDENCE ?= $(DOCKER)/test-results
WS := /home/mr2/mr2-stack/rover/ros2_ws
ROS := /opt/mr2/with-ros.sh
s ?= rover

.PHONY: help check local local-viewer up down ps build rebuild-images shell \
        logs ready mission pose reload-nginx test clean-run clean-volumes

help:
	@sed -n '1,5p' $(DOCKER)/Makefile
	@echo "Other targets: build mission pose ready test clean-run shell logs"

check:
	@if git -C $(REPO) submodule status | grep -q '^-'; then \
	   echo "submodules missing: git submodule update --init --recursive"; \
	   exit 1; fi

local: check
	$(COMPOSE) up -d --build --wait

local-viewer: check
	$(COMPOSE) --profile viewer up -d --build --wait

up: check
	$(COMPOSE) up -d $(SERVICES)

down:
	$(COMPOSE) --profile viewer down

ps:
	$(COMPOSE) --profile viewer ps

rebuild-images: check
	$(COMPOSE) --profile viewer build

# Rebuild the ROS workspace inside the running rover container after editing
# rover/ros2_ws/src (the build lives in shared volumes), then restart autonomy.
build:
	$(COMPOSE) exec rover $(ROS) bash -c 'cd $(WS) && MAKEFLAGS=-j6 \
	  colcon build --executor sequential --symlink-install'
	$(COMPOSE) restart autonomy

shell:
	$(COMPOSE) exec $(s) $(ROS) bash

logs:
	$(COMPOSE) logs -f --tail 200 $(s)

ready:
	-$(COMPOSE) exec rover $(ROS) python3 /opt/mr2/ready.py rover
	-$(COMPOSE) exec autonomy $(ROS) python3 /opt/mr2/ready.py autonomy

# One GNSS mission (~3 m north by default): make mission ARGS="--lat 38.406829"
mission:
	$(COMPOSE) exec autonomy $(ROS) python3 /opt/mr2/mission.py $(ARGS)

# Rover ground-truth pose from Gazebo
pose:
	$(COMPOSE) exec rover $(ROS) python3 /opt/mr2/gz_pose.py

# nginx keeps backend addresses from start-up; reload after recreating them.
reload-nginx:
	$(COMPOSE) exec nginx nginx -s reload

test:
	COMPOSE_FILE=$(DOCKER)/compose/local/compose.local.yaml \
	  $(DOCKER)/tests/run_suite.sh $(EVIDENCE)/run$(or $(N),0)

# Clean run: drop containers and volumes, rebuild, start, run the suite.
clean-run: check
	$(COMPOSE) --profile viewer down -v
	$(COMPOSE) up -d --build --wait
	$(MAKE) test N=$(N)

clean-volumes:
	$(COMPOSE) --profile viewer down -v
