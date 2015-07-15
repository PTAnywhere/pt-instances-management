"""
Created on 13/07/2015

@author: Aitor Gomez Goiri <aitor.gomez-goiri@open.ac.uk>
"""

from flask import redirect, request, render_template, url_for, jsonify
from werkzeug.exceptions import BadRequest
from ptinstancemanager.app import app
from ptinstancemanager.models import Instance, Port


@app.route("/")
def index():
    return "Hello World!"


@app.errorhandler(404)
def not_found(error=None):
    message = {
        'status': 404,
        'message': 'Not Found: %s.\n%s' % (request.url, error),
    }
    resp = jsonify(message)
    resp.status_code = 404
    return resp


@app.route("/details")
def get_configuration_details():
    return jsonify( lowest_port=app.config['LOWEST_PORT'],
                    highest_port=app.config['HIGHEST_PORT'] )


@app.route("/instances")
def list_instances():
    show_param = request.args.get("show")
    if show_param is None or show_param == "running":  # default option
        return jsonify(instances=[ins.serialize for ins in Instance.get_running()])
    else:
        if show_param not in ("all", "finished"):
            return BadRequest("The 'show' parameter must contain one of the following values: all, running or finished.")

        if show_param == "all":
            return jsonify(instances=[ins.serialize for ins in Instance.get_all()])  # .limit(10)
        else:  # show_param is "finished":
            return jsonify(instances=[ins.serialize for ins in Instance.get_finished()])


@app.route("/instances", methods=['POST'])
def create_instance():
    # return "%r" % request.get_json()
    available_port = Port.allocate()
    
    # TODO create container with Docker
    # if success
    instance = Instance.create(available_port.number)
    available_port.assign(instance.id)

    # If sth went wrong: available_port.release()
    # Return appropriate HTTP error
    return jsonify(instance.serialize)


@app.route("/instances/<instance_id>")
def show_instance_details(instance_id):
    instance = Instance.get(instance_id)
    if instance is None:
        return not_found(error="The instance does not exist.")
    return jsonify(instance.serialize)


@app.route("/instances/<instance_id>", methods=['DELETE'])
def stop_instance(instance_id):
    instance = Instance.stop(instance_id)
    if instance is None:
        return not_found(error="The instance does not exist.")
    Port.get(instance.pt_port).release()  # The port can be now reused by a new PT instance
    return jsonify(instance.serialize)  


@app.route("/ports")
def list_ports():
    show_param = request.args.get("show")
    if show_param is None or show_param == "all":
        return jsonify(ports=[port.serialize for port in Port.get_all()])
    else:
        if show_param not in ("available", "unavailable"):
            return BadRequest("The 'show' parameter must contain one of the following values: all, available or unavailable.")

        if show_param == "available":
            return jsonify(ports=[port.serialize for port in Port.get_available()])
        else:  # show_param is "unavailable":
            return jsonify(ports=[port.serialize for port in Port.get_unavailable()])