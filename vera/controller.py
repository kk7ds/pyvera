import json
try:
    import urllib2
except ImportError:
    import urllib as urllib2

class DeviceNotFound(Exception):
    pass


class NoInterfaceForDevice(Exception):
    pass


class Attribute(object):
    @classmethod
    def from_json(cls, json):
        self = cls()
        self.id = json.get('id')
        self.service = json.get('service')
        self.value = json.get('value')
        self.variable = json.get('variable')

        try:
            self.value = int(self.value)
        except ValueError:
            pass

        return self


class Interface(object):
    SERVICE = 'none'

    def __init__(self, device):
        self._device = device

    def action(self, action, **kwargs):
        devnum = int(self._device._devdata['id'])
        result = self._device._controller.data_request(
            'action',
            DeviceNum=devnum,
            serviceId=self.SERVICE,
            action=action,
            **kwargs)
        return result


class SwitchInterface(Interface):
    SERVICE = 'urn:upnp-org:serviceId:SwitchPower1'

    def set_state(self, state):
        self.action('SetTarget', newTargetValue=state)
        # FIXME: This shouldn't be immediate in case it didn't work
        self._device.attributes['Status'].value = int(state)

    def turn_on(self):
        self.set_state(1)

    def turn_off(self):
        self.set_state(0)

    def toggle(self):
        self.set_state(int(not self.state))

    @property
    def state(self):
        return bool(self._device.attributes['Status'].value)


class DimmerInterface(Interface):
    SERVICE = 'urn:upnp-org:serviceId:Dimming1'

    @property
    def level(self):
        return self._device.attributes['LoadLevelStatus'].value

    def set_level(self, level):
        self.action('SetLoadLevelTarget', newLoadlevelTarget=level)
        # FIXME: This shouldn't be immediate in case it didn't work
        self._device.attributes['LoadLevelStatus'].value = level
        self._device.attributes['LoadLevelTarget'].value = level


class InterfaceDispatcher(object):
    def __init__(self, device):
        self._device = device

    def __getattr__(self, name):
        try:
            return self._device._interfaces[name]
        except KeyError:
            raise NoInterfaceForDevice()

    def __hasattr__(self, name):
        return name in self._device._interfaces

    def __contains__(self, name):
        return name in self._device._interfaces


INTERFACES = [SwitchInterface, DimmerInterface]


class Device(object):
    def __init__(self, controller, devdata):
        self._controller = controller
        self._devdata = devdata
        self._interfaces = {}
        self.i = InterfaceDispatcher(self)

    def add_interface(self, interface_cls):
        interface = interface_cls(self)
        name = interface_cls.__name__.lower().replace('interface', '')
        self._interfaces[name] = interface

    def detect_interfaces(self):
        services = [x.service for x in self.attributes.values()]
        for interface_cls in INTERFACES:
            if interface_cls.SERVICE in services:
                yield interface_cls

    @property
    def name(self):
        return self._devdata['name']

    @property
    def id(self):
        return int(self._devdata['id'])

    @property
    def room_id(self):
        return int(self._devdata['room'])

    @property
    def room(self):
        return self._controller.rooms.get(self.room_id, 'UNKNOWN')

    @classmethod
    def from_json(cls, controller, devdata, json):
        self = cls(controller, devdata)
        self.attributes = {}
        for state in json.get('states', []):
            attr = Attribute.from_json(state)
            self.attributes[attr.variable] = attr
        for interface_cls in self.detect_interfaces():
            self.add_interface(interface_cls)

        return self

    def __repr__(self):
        if hasattr(self.i, 'switch'):
            status = self.i.switch.state
        else:
            status = None
        return 'Device<%s>(status=%s,room=%s)' % (self.name, status,
                                                  self.room)

    def dump(self):
        return ('%s:%s' % (self.name, self._devdata['id']),
                {k: v.value for k, v in self.attributes.items()})

    def refresh(self, only=None):
        current = self._controller.get_device(self.id)
        if only is None:
            self.attributes = current.attributes
        else:
            for name in only:
                self.attributes[name] = current.attributes[name]


class Controller(object):
    def __init__(self, ip, port):
        self._ip = ip
        self._port = port
        self._device_data = None

    def data_request(self, id_, **params):
        params['output_format'] = 'json'
        query_string = '&'.join(['%s=%s' % (k, v)
                                 for k, v in params.items()])
        url = 'http://%s:%s/data_request?id=%s&%s' % (
            self._ip, self._port, id_, query_string)
        req = urllib2.urlopen(url, timeout=10)
        response = json.loads(req.read())
        response['_http_code'] = req.getcode()
        response['_http_info'] = req.info()
        return response

    @property
    def _devices(self):
        if self._device_data:
            return self._device_data
        self._device_data = self.data_request('user_data')
        return self._device_data

    @property
    def rooms(self):
        rooms = {0: 'No Room'}
        for room in self._devices['rooms']:
            rooms[room['id']] = room['name']
        return rooms

    def _device_data_by_id(self, id_):
        for device in self._devices['devices']:
            if int(device['id']) == id_:
                return device
        raise DeviceNotFound('Device %i Not found' % id_)

    def get_device(self, devnum):
        data = self.data_request('status', DeviceNum=devnum)
        devdata = self._device_data_by_id(devnum)
        return Device.from_json(self, devdata, data['Device_Num_%i' % devnum])

    def get_device_by_name(self, name):
        for device in self._devices['devices']:
            if device['name'] == name:
                return self.get_device(int(device['id']))
        raise DeviceNotFound('Device %s Not Found' % name)

    def get_all(self, include_controller=False):
        data = self.data_request('status')
        devices = []
        for device in data['devices']:
            devdata = self._device_data_by_id(int(device['id']))
            if devdata['name'] == 'ZWave' and not include_controller:
                continue
            try:
                devices.append(Device.from_json(
                    self, devdata, device))
            except DeviceNotFound:
                print('Missed device %s' % device['id'])
                pass
        return devices
