import httplib
import json
import logging
import pprint
import random
import socket
import time

import donatello

from vera import controller


class EventPoll(object):
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._data_version = 0
        self._load_time = int(time.time())
        self._log = logging.getLogger('EventPoll')
        initial = self.poll(min_delay=0, timeout=0)

    def poll(self, min_delay=500, timeout=10):
        rand = random.random()
        c = httplib.HTTPConnection(self._host, self._port)

        url = ('/data_request?id=lu_status'
               '&DataVersion=%i'
               '&MinimumDelay=%i'
               '&Timeout=%i'
               '&LoadTime=%i'
               '&rand=%f' % (self._data_version,
                             min_delay,
                             timeout,
                             self._load_time,
                             rand))
        c.request('GET', url)
        resp = c.getresponse()
        respdata = json.loads(resp.read().decode())
        self._data_version = respdata['DataVersion']
        self._load_time = respdata['LoadTime']
        return respdata


class EventPollSender(object):
    def __init__(self, ep, controller_):
        self._disconnected = False
        self._ep = ep
        self._controller = controller_
        self._devices = {}
        self._log = logging.getLogger('EventPollSender')

    def _get_device(self, ident):
        if ident not in self._devices:
            self._devices[ident] = self._controller.get_device(ident)
        return self._devices[ident]

    def process_ArmedTripped(self, device, value, changed_vars):
        self._log.info('Device %s tripped: %i' % (device.name, int(value)))
        event = {'sender': 'devices',
                 'device': device.name,
                 'motion_detected': int(value),
             }
        donatello.Events.send_event(event)

    def process_Watts(self, device, value, changed_vars):
        self._log.info('Device %s using %.2f watts' % (device.name,
                                                       float(value)))
        event = {'sender': 'devices',
                 'device': device.name,
                 'current_power': float(value),
             }
        donatello.Events.send_event(event)

    def process_KWH(self, device, value, changed_vars):
        self._log.info('Device %s has used %.2f KWH' % (device.name,
                                                        float(value)))

    def process_Status(self, device, value, changed_vars):
        self._log.info('Device %s is now %s' % (device.name,
                                          int(value) and 'on' or 'off'))
        event = {'sender': 'devices',
                 'device': device.name,
                 'reason': 'state',
                 'previous-state': not bool(int(value)),
                 'state': bool(int(value)),
             }
        donatello.Events.send_event(event)

    def process_CurrentTemperature(self, device, value, changed_vars):
        self._log.info('Device %s is now %sF' % (device.name, value))
        event = {'sender': 'devices',
                 'device': device.name,
                 'level': float(value),
             }
        donatello.Events.send_event(event)

    def process_CurrentLevel(self, device, value, changed_vars):
        self._log.info('Device %s is now %s' % (device.name, value))
        event = {'sender': 'devices',
                 'device': device.name,
                 'level': float(value),
             }
        donatello.Events.send_event(event)

    def process_sl_SceneActivated(self, device, value, changed_vars):
        self._log.info('Remote %s activated button %s' % (device.name, value))
        event = {'sender': 'devices',
                 'device': device.name,
                 'button': int(value)}
        donatello.Events.send_event(event)

    def _process_devices(self, devices):
        for dev in devices:
            try:
                device = self._get_device(dev['id'])
            except controller.DeviceNotFound:
                self._log.error('Device %i not found, '
                                'ignoring event' % dev['id'])
                continue
            changed_vars = {x['variable']: x['value']
                            for x in dev.get('states', [])}
            if not changed_vars:
                continue

            self.device_event_hook(device, changed_vars)
            really_changed = False
            for var in changed_vars:
                try:
                    attr_type = type(device.attributes[var].value)
                    real_value = attr_type(changed_vars[var])
                    if 'sl_SceneActivated' in changed_vars:
                        # Ignore seemingly unchanged scene activations
                        pass
                    elif real_value == device.attributes[var].value:
                        continue

                    if hasattr(self, 'process_%s' % var):
                        getattr(self, 'process_%s' % var)(device,
                                                          changed_vars[var],
                                                          changed_vars)
                    device.attributes[var].value = real_value
                    really_changed = True
                except Exception:
                    self._log.exception('Processing %s.%s' % (
                        device.name, var))

            if really_changed:
                self._log.debug('Device %i changed %r' % (dev['id'],
                                                          changed_vars))


    def device_event_hook(self, device, changed_vars):
        """Called when a device has changed data.

        :returns:True if updates were really found
        """
        return True

    def run(self):
        while True:
            try:
                if self._disconnected:
                    delay = 10000
                    timeout = 15
                    self._log.warning('Last poll failed, doing long poll')
                    time.sleep(1)
                else:
                    delay = 500
                    timeout = 10
                data = self._ep.poll(min_delay=delay,
                                     timeout=timeout)
                if self._disconnected:
                    self._log.warning('First successful request, ignoring')
                    self._disconnected = False
                    continue
            except ValueError as e:
                self._log.warning('Failed to poll: %s' % e)
                self._disconnected = True
                continue
            except httplib.BadStatusLine as e:
                self._log.error('Bad HTTP response while polling: %s' % e)
                self._disconnected = True
            except socket.error as e:
                self._log.error('Socket error while polling: %s' % e)
                self._disconnected = True
                continue
            except Exception as e:
                self._log.exception('Unknown error while polling: %s' % e)
                self._disconnected = True
                continue
            if 'devices' in data:
                self._process_devices(data['devices'])
            if 'tasks' in data:
                self.log('Tasks: %s' % data['tasks'])
