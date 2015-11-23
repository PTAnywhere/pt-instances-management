import logging
from docker import Client
from docker.errors import APIError
from celery import chain

import ptchecker
from ptinstancemanager.app import app, celery
from ptinstancemanager.models import Instance, Port, CachedFile



def create_containers(num_containers):
    logging.info('Creating new containers.')
    for _ in range(num_containers):
        available_port = Port.allocate()

        if available_port is None:
            raise Exception('The server cannot create new instances. Please, wait and retry it.')

        res = create_container.apply_async((available_port.number,), link=wait_for_ready_container.s())


@celery.task()
def create_container(pt_port):
    """Runs a new packettracer container in the specified port and
        create associated instance."""
    logging.info('Creating new container.')

    # Create container with Docker
    vnc_port = pt_port + 10000
    container_id = start_container(pt_port, vnc_port)

    # If success...
    instance = Instance.create(container_id, pt_port, vnc_port)
    port = Port.get(pt_port)
    port.assign(instance.id)

    logging.info('Container started: %s' % container_id)

    return instance.id


#@celery.task()
def start_container(pt_port, vnc_port):
    """Creates and starts new packettracer container with Docker."""
    docker = Client(app.config['DOCKER_URL'], version='auto')
    port_bindings = { app.config['DOCKER_PT_PORT']: pt_port,
                      app.config['DOCKER_VNC_PORT']: vnc_port }
    vol_bindings = { app.config['CACHE_DIR']:
                    {'bind': app.config['CACHE_CONTAINER_DIR'], 'mode': 'ro'} }
    host_config = docker.create_host_config(
                                port_bindings=port_bindings,
                                binds=vol_bindings,
                                volumes_from=(app.config['DOCKER_DATA_ONLY'],))
    container = docker.create_container(image=app.config['DOCKER_IMAGE'],
                                        ports=list(port_bindings.keys()),
                                        volumes=[vol_bindings[k]['bind'] for k in vol_bindings],
                                        host_config=host_config)

    if container.get('Warnings'):
        raise Exception('Error during container creation: %s' % container.get('Warnings'))

    # If success...
    response = docker.start(container=container.get('Id'))  # TODO log response?

    return container.get('Id')


@celery.task()
def assign_container():
    """Unpauses available container and marks associated instance as assigned."""
    logging.info('Assigning container.')
    docker = Client(app.config['DOCKER_URL'], version='auto')
    for instance in Instance.get_unassigned():
    	try:
    	    docker.unpause(instance.docker_id)
    	    instance.assign()
            return instance.id
        except APIError as ae:
            logging.error('Error assigning instance %s.' % instance.id)
            logging.error('Docker API exception. %s.' % ae)
    	    # e.g., if it was already unpaused or it has been stopped
    	    instance.mark_error()
            monitor_containers.delay()


@celery.task()
def unassign_container(instance_id):
    """Marks instance as unassigned and pauses the associated container."""
    instance = Instance.get(instance_id)
    docker = Client(app.config['DOCKER_URL'], version='auto')
    try:
        docker.pause(instance.docker_id)
        instance.unassign()
    except APIError as ae:
        logging.error('Error unassigning instance %s.' % instance_id)
        logging.error('Docker API exception. %s.' % ae)
    	# e.g., if it was already paused
    	instance.mark_error()
        monitor_containers.delay()


@celery.task(max_retries=5)
def wait_for_ready_container(instance_id, timeout=30):
    """Waits for an instance to be ready (e.g., answer).
        Otherwise, marks it as erroneous ."""
    logging.info('Waiting for container to be ready.')
    instance = Instance.get(instance_id)
    is_running = ptchecker.is_running(app.config['PT_CHECKER'], 'localhost', instance.pt_port, float(timeout))
    if is_running:
        unassign_container.delay(instance_id)  # else
    else:
        instance.mark_error()
        monitor_containers.delay()
	    # raise wait_for_ready_container.retry(exc=Exception('The container has not answered yet.'))


@celery.task()
def monitor_containers():
    logging.info('Monitoring instances.')
    restarted_instances = []
    docker = Client(app.config['DOCKER_URL'], version='auto')
    try:
        # Restart stopped containers (which exited successfully)
    	for container in cli.containers(filters={'exited': 0, 'label': 'ancestor=packettracer'}):
            container_id = container.get('Id')
            instance = Instance.get_by_docker_id(container_id)
            if instance:
                logging.info('Restarting %s.' % instance)
                restarted_instances.append(instance.id)
                instance.mark_starting()
                docker.start(container=container_id)
                wait_for_ready_container.delay(instance_id)

        for erroneous_instance in Instance.get_errors():
            if not erroneous_instance.docker_id in restarted_instances:
                logging.info('Deleting erroneous %s.' % instance)
                instance.delete()
            	Port.get(instance.pt_port).release()
                # Very conservative approach:
                #   we remove it even if it might still be usable.
                remove_container.delay(erroneous_instance.docker_id)
                # TODO replace erroneous instance by a new one
    except APIError as ae:
        logging.error('Error on container monitoring.')
        logging.error('Docker API exception. %s.' % ae)
    finally:
        return restarted_instances



@celery.task()
def remove_container(docker_id):
    docker = Client(app.config['DOCKER_URL'], version='auto')
    try:
        # TODO first check its status and then act? (e.g., to unpause it before)
        docker.remove_container(docker_id, force=True)
    except APIError as ae:
        logging.error('Error on container removal: %s.' % docker_id)
        logging.error('Docker API exception. %s.' % ae)
