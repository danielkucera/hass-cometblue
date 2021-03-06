"""
Support for Eurotronic CometBlue thermostats.
They are identical to the Xavax Bluetooth thermostats and others, e.g. sold by discounters.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/climate.cometblue/
"""
import logging
from datetime import timedelta
from datetime import datetime
import threading
import voluptuous as vol

from sys import stderr

from homeassistant.components.climate import (
    ClimateDevice,
    PLATFORM_SCHEMA,
    STATE_ON,
    STATE_OFF,
    SUPPORT_TARGET_TEMPERATURE_HIGH,
    SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_OPERATION_MODE)
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
    CONF_PIN,
    CONF_DEVICES,
    TEMP_CELSIUS,
    ATTR_TEMPERATURE,
    PRECISION_HALVES)

import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['cometblue']

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(10)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=300)
SCAN_INTERVAL = timedelta(seconds=300)

STATE_AUTO_LOCKED = "auto_locked"
STATE_AUTO = "auto"
STATE_MANUAL = "manual"
STATE_MANUAL_LOCKED = "manual_locked"

ATTR_STATE_WINDOW_OPEN = 'window_open'
ATTR_STATE_VALVE = 'valve'
ATTR_STATE_LOCKED = 'is_locked'
ATTR_STATE_LOW_BAT = 'low_battery'
ATTR_BATTERY = 'battery_level'
ATTR_TARGET = 'target_temp'
ATTR_VENDOR_NAME = 'vendor_name'
ATTR_MODEL = 'model'
ATTR_FIRMWARE = 'firmware'
ATTR_VERSION = 'version'
ATTR_WINDOW = 'window_open'

DEVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_PIN, default=0): cv.positive_int,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES):
        vol.Schema({cv.string: DEVICE_SCHEMA}),
})

SUPPORT_FLAGS = (SUPPORT_OPERATION_MODE)

from cometblue import device as cometblue_dev

gatt_mgr = None


def setup_platform(hass, config, add_devices, discovery_info=None):
    global gatt_mgr
    _LOGGER.debug("setup cometblue")

    gatt_mgr = cometblue_dev.CometBlueManager('hci0')

    class ManagerThread(threading.Thread):
        def run(self):
            gatt_mgr.run()

    ManagerThread().start()

    devices = []

    for name, device_cfg in config[CONF_DEVICES].items():
        _LOGGER.debug("adding device: {}".format(name))
        dev = CometBlueThermostat(device_cfg[CONF_MAC], name, device_cfg[CONF_PIN])
        devices.append(dev)

    add_devices(devices)


class CometBlueStates():
    BIT_MANUAL = 0x01
    BIT_LOCKED = 0x80
    BIT_WINDOW = 0x10

    def __init__(self):
        self._temperature = None
        self.target_temp = None
        self.manual = None
        self.locked = None
        self.window = None
        self._battery_level = None
        self.manufacturer = None
        self.software_rev = None
        self.firmware_rev = None
        self.model_no = None
        self.name = None
        self.last_seen = None
        self.last_talked = None

    @property
    def temperature_value(self):
        val = {
            'manual_temp': self.target_temp,
            'current_temp': self.temperature,
            'target_temp_l': 16,
            'target_temp_h': 21,
            'offset_temp': 0.0,
            'window_open_detection': 12,
            'window_open_minutes': 10
        }
        return val

    @property
    def mode_value(self):
        val = {
            'not_ready': None,
            'childlock': self.locked,
            'state_as_dword': None,
            'manual_mode': self.manual,
            'adapting': None,
            'unused_bits': None,
            'low_battery': None,
            'antifrost_activated': None,
            'motor_moving': None,
            'installing': None,
            'satisfied': None
        }
        return val

    @mode_value.setter
    def mode_value(self, value):
        self.manual = value['manual_mode']
        self.window = False
        self.locked = value['childlock']

    @property
    def mode_code(self):
        if self.manual is None or self.locked is None:
            return None
        if self.manual:
            if self.locked:
                return STATE_MANUAL_LOCKED
            else:
                return STATE_MANUAL
        else:
            if self.locked:
                return STATE_AUTO_LOCKED
            else:
                return STATE_AUTO

    @mode_code.setter
    def mode_code(self, value):
        if value == STATE_MANUAL:
            self.manual = True
            self.locked = False
        elif value == STATE_MANUAL_LOCKED:
            self.manual = True
            self.locked = True
        elif value == STATE_AUTO:
            self.manual = False
            self.locked = False
        elif value == STATE_AUTO_LOCKED:
            self.manual = False
            self.locked = True

    @property
    def battery_level(self):
        return self._battery_level

    @battery_level.setter
    def battery_level(self, value):
        if value is not None and 0 <= value <= 100:
            self._battery_level = value

    @property
    def temperature(self):
        return self._temperature

    @temperature.setter
    def temperature(self, value):
        if value is not None and 8 <= 28:
            self._temperature = value


class CometBlueThermostat(ClimateDevice):
    """Representation of a CometBlue thermostat."""

    def __init__(self, _mac, _name, _pin=None):
        """Initialize the thermostat."""

        global gatt_mgr

        self.modes = [STATE_AUTO, STATE_AUTO_LOCKED, STATE_MANUAL, STATE_MANUAL_LOCKED]
        self._mac = _mac
        self._name = _name
        self._pin = _pin
        self._thermostat = cometblue_dev.CometBlue(_mac, gatt_mgr, _pin)
        self._target = CometBlueStates()
        self._current = CometBlueStates()
        self.update()

    # def __del__(self):
    #    self._thermostat.disconnect()

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def available(self) -> bool:
        """Return if thermostat is available."""
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement that is used."""
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return cometblue's precision 0.5."""
        return PRECISION_HALVES

    @property
    def current_temperature(self):
        """Return current temperature"""
        return self._current.temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target.target_temp

    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if temperature < self.min_temp:
            temperature = self.min_temp
        if temperature > self.max_temp:
            temperature = self.max_temp
        self._target.target_temp = temperature

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        # return self._thermostat.min_temp
        return 8.0

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        # return self._thermostat.max_temp
        return 28.0

    @property
    def current_operation(self):
        """Current mode."""
        return self._current.mode_code

    @property
    def operation_list(self):
        """List of available operation modes."""
        return self.modes

    def set_operation_mode(self, operation_mode):
        """Set operation mode."""
        self._target.mode_code = operation_mode

    def is_stale(self):
        _LOGGER.info(
            "{} last seen {} last talked {}".format(self._mac, self._current.last_seen, self._current.last_talked))
        now = datetime.now()
        if self._current.last_seen is not None and (now - self._current.last_seen).total_seconds() < 600:
            return False
        if self._current.last_talked is not None and (now - self._current.last_talked).total_seconds() < 600:
            return False
        return True

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        if self.is_stale():
            return 'mdi:bluetooth-off'
        if self._current.battery_level is None:
            return 'mdi:bluetooth-off'
        if self._current.battery_level == 100:
            return 'mdi:battery'
        if self._current.battery_level == 0:
            return 'mdi:battery-alert'
        if self._current.battery_level < 10:
            return 'mdi:battery-outline'
        if 10 <= self._current.battery_level <= 99:
            return 'mdi:battery-{}0'.format(int(self._current.battery_level / 10))
        return None

    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""
        return {
            ATTR_VENDOR_NAME: self._current.manufacturer,
            ATTR_MODEL: self._current.model_no,
            ATTR_FIRMWARE: self._current.firmware_rev,
            ATTR_VERSION: self._current.software_rev,
            ATTR_BATTERY: self._current.battery_level,
            ATTR_TARGET: self._current.target_temp,
            ATTR_WINDOW: self._current.window,
        }

    def update(self):
        """Update the data from the thermostat."""
        get_temperatures = True
        _LOGGER.info("Update called {}".format(self._mac))
        self._thermostat.connect()
        self._thermostat.attempt_to_get_ready()
        with self._thermostat as device:
            if self._current.mode_code != self._target.mode_code and self._target.manual is not None:
                _LOGGER.debug("Setting mode to: {}".format(self._target.mode_value))
                device.set_status(self._target.mode_value)
            if self._current.target_temp != self._target.target_temp and self._target.target_temp is not None:
                # TODO: Fix temperature settings. Currently not working.

                _LOGGER.info("Values to set: {}".format(str(self._target.temperature_value)))
                _LOGGER.debug("Setting temperature to: {}".format(self._target.target_temp))
                device.set_temperatures(self._target.temperature_value)
                get_temperatures = False
            cur_batt = device.get_battery()
            _LOGGER.debug("Current Battery Level: {}%".format(cur_batt))
            cur_status = device.get_status()
            cur_temps = device.get_temperatures()
            if cur_temps['current_temp'] != -64.0:
                self._current.temperature = cur_temps['current_temp']
            self._current.target_temp = cur_temps['manual_temp']
            _LOGGER.debug("Current Temperature: {}".format(cur_temps))
            if self._current.model_no is None:
                self._current.model_no = device.get_model_number()
                self._current.firmware_rev = device.get_firmware_revision()
                self._current.software_rev = device.get_software_revision()
                self._current.manufacturer = device.get_manufacturer_name()
                _LOGGER.debug("Current Mode: {}".format(cur_status))
                _LOGGER.debug("Current Model Number: {}".format(self._current.model_no))
                _LOGGER.debug("Current Firmware Revision: {}".format(self._current.firmware_rev))
                _LOGGER.debug("Current Software Revision: {}".format(self._current.software_rev))
                _LOGGER.debug("Current Manufacturer Name: {}".format(self._current.manufacturer))
        self._thermostat.disconnect()
        if self._current.target_temp is not None:
            self._target.target_temp = self._current.target_temp
        self._current.battery_level = cur_batt
        self._current.mode_value = cur_status
        self._current.last_seen = datetime.now()
        self._current.last_talked = datetime.now()
