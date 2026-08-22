.PHONY: init up up-all down status logs ports doctor smoke switch-regression benchmark test lint models k8s-render

init:
	./hub init

up:
	./hub up

up-all:
	./hub up --agent --observability

down:
	./hub down

status:
	./hub status

logs:
	./hub logs

ports:
	./hub ports

doctor:
	./hub doctor --gpu

smoke:
	./hub smoke

switch-regression:
	./hub switch-regression 25

benchmark:
	./hub benchmark 3

test:
	./hub test

lint:
	./hub lint

models:
	./hub model list

k8s-render:
	./hub k8s render
