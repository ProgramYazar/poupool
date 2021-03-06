import transitions
import time
import logging
import re
import serial
import io
import subprocess
from abc import abstractmethod
from .util import mapping, constrain

logger = logging.getLogger(__name__)


class SensorError(Exception):
    pass


class DeviceRegistry(object):

    def __init__(self):
        self.__valves = {}
        self.__pumps = {}
        self.__sensors = {}

    def get_valves(self):
        return self.__valves.values()

    def get_pumps(self):
        return self.__pumps.values()

    def get_sensors(self):
        return self.__sensors.values()

    def add_valve(self, device):
        self.__valves[device.name] = device

    def add_pump(self, device):
        self.__pumps[device.name] = device

    def add_sensor(self, device):
        self.__sensors[device.name] = device

    def get_valve(self, name):
        return self.__valves.get(name)

    def get_pump(self, name):
        return self.__pumps.get(name)

    def get_sensor(self, name):
        return self.__sensors.get(name)


class Device(object):

    def __init__(self, name):
        self.name = name


class SwitchDevice(Device):

    def __init__(self, name, gpio, pin):
        super().__init__(name)
        self.__gpio = gpio
        self.pin = pin
        self.__gpio.setup(self.pin, self.__gpio.OUT)
        self.__gpio.output(self.pin, True)

    def on(self):
        logger.debug("Switch %s (%d) set to ON" % (self.name, self.pin))
        self.__gpio.output(self.pin, False)

    def off(self):
        logger.debug("Switch %s (%d) set to OFF" % (self.name, self.pin))
        self.__gpio.output(self.pin, True)


class PumpDevice(Device):

    def __init__(self, name, gpio, pins):
        super().__init__(name)
        self.__gpio = gpio
        assert len(pins) == 4
        self.pins = pins
        self.__gpio.setup(self.pins, self.__gpio.OUT)
        self.__gpio.output(self.pins, True)

    def on(self):
        self.speed(3)

    def off(self):
        self.speed(0)

    def speed(self, value):
        assert 0 <= value <= 3
        values = [i != value for i in range(len(self.pins))]
        logger.debug("Pump %s speed %d (%s:%s)" % (self.name, value, str(self.pins), str(values)))
        self.__gpio.output(self.pins, values)


class SensorDevice(Device):

    def __init__(self, name):
        super().__init__(name)

    @property
    @abstractmethod
    def value(self):
        pass


class TempSensorDevice(SensorDevice):

    CRE = re.compile(" t=(-?\d+)$")

    def __init__(self, name, address, offset=0.0):
        super().__init__(name)
        self.__address = address
        self.__path = "/sys/bus/w1/devices/%s/w1_slave" % address
        self.__offset = offset

    def __read_temp_raw(self):
        with open(self.__path, "r") as f:
            return [line.strip() for line in f.readlines()]

    @property
    def value(self):
        # Retry up to 3 times
        try:
            for _ in range(3):
                raw = self.__read_temp_raw()
                if len(raw) == 2:
                    crc, data = raw
                    if crc.endswith("YES"):
                        logger.debug("Temp sensor raw data: %s" % str(data))
                        # CRC valid, read the data
                        match = TempSensorDevice.CRE.search(data)
                        temperature = int(match.group(1)) / 1000. + self.__offset if match else None
                        # Range check, sometimes bad values pass the CRC check
                        if -20 < temperature < 80:
                            return temperature
                        else:
                            logger.debug("Temp outside range: %f" % temperature)
                    else:
                        logger.debug("Bad CRC: %s" % str(raw))
                time.sleep(0.1)
        except OSError:
            logger.exception("Unable to read temperature (%s)" % self.name)
        return None


class TankSensorDevice(SensorDevice):

    def __init__(self, name, adc, channel, gain, low, high):
        super().__init__(name)
        self.__adc = adc
        self.__channel = channel
        self.__gain = gain
        self.__low = low
        self.__high = high

    @property
    def value(self):
        values = []
        for _ in range(10):
            try:
                values.append(self.__adc.read_adc(self.__channel, gain=self.__gain))
                time.sleep(0.05)
            except OSError:
                logger.exception("Unable to read ADC %s" % self.name)
                time.sleep(0.5)
        # In case we got really no readings, we return 0 in order for the system to go into
        # emergency stop.
        value = sum(values) / len(values) if values else 0
        logger.debug("Tank sensor average ADC=%.2f" % value)
        return constrain(mapping(value, self.__low, self.__high, 0, 100), 0, 100)


class EZOSensorDevice(SensorDevice):

    def __init__(self, name, port):
        super().__init__(name)
        self.__serial = serial.Serial(port, timeout=0.1)
        self.__sio = io.TextIOWrapper(io.BufferedRWPair(self.__serial, self.__serial))
        info = self.__send("i")
        logger.info("EZO sensor %s says: %s" % (name, info))
        # Disable continuous mode
        self.__send("C,0")
        if self.__send("C,?") == "?C,0":
            logger.debug("Disabled continuous readings")
        else:
            logger.error("Unable to disable continuous readings for %s" % name)

    def __reconnect(self):
        self.__serial.close()
        time.sleep(5)
        self.__serial.open()

    @property
    def value(self):
        # This can block for up to ~1000ms
        value = self.__send("R")
        return float(value) if value else None

    def __send(self, value):
        try:
            # send
            self.__sio.write(value + "\r")
            self.__sio.flush()
            # receive
            response = None
            read = self.__sio.readline()
            while not read.startswith("*"):
                # Only keep the last line of the response
                response = read.strip()
                read = self.__sio.readline()
            if read.strip() == "*OK":
                return response
            else:
                logger.error("Bad response: %s" % read.strip())
        except Exception:
            # We catch everything in the hope that we recover with a reconnect.
            logger.exception("Serial sensor %s had an error. Reconnecting..." % self.name)
            self.__reconnect()
        return None


class ArduinoDevice(Device):

    def __init__(self, name, port):
        super().__init__(name)
        # Disable hangup-on-close to avoid having the Arduino resetting when closing the
        # connection. Useful for debugging and to avoid interrupting a move.
        # https://playground.arduino.cc/Main/DisablingAutoResetOnSerialConnection
        subprocess.check_call(["stty", "-F", port, "-hupcl"])
        self.__serial = serial.Serial(port, baudrate=9600, timeout=0.1)
        self.__sio = io.TextIOWrapper(io.BufferedRWPair(self.__serial, self.__serial))

    def __reconnect(self):
        self.__serial.close()
        time.sleep(5)
        self.__serial.open()

    @property
    def cover_position(self):
        value = self.__send("position")
        return int(value.replace("position ", "")) if value else None

    def cover_open(self):
        self.__send("open")

    def cover_close(self):
        self.__send("close")

    def cover_stop(self):
        self.__send("stop")

    @property
    def water_counter(self):
        value = self.__send("water")
        return int(value.replace("water ", "")) if value else None

    def off(self):
        # This is to be compatible with the sensor API. All devices are turned off() when exiting
        # the application. We stop the cover.
        self.cover_stop()

    def __send(self, value):
        try:
            # flush buffer (should be empty but we can receive an "emergency stop")
            logger.debug("Flushing read buffer")
            read = self.__sio.readline()
            while not read.strip() == "":
                logger.error("Unexpected buffer content: %s" % read.strip())
                read = self.__sio.readline()
            # send
            logger.debug("Writing '%s' to serial port" % value)
            self.__sio.write(value + "\n")
            self.__sio.flush()
            # receive
            logger.debug("Reading response")
            response = None
            counter = 0
            read = self.__sio.readline()
            while not read.startswith("***") and counter < 20:
                # Only keep the last line of the response
                response = read.strip()
                read = self.__sio.readline()
                counter += 1
            logger.debug("Received response")
            if read.strip() == "***" and response.startswith(value):
                return response
            else:
                logger.error("Bad response: %s %s" % (response, read.strip()))
        except Exception:
            # We catch everything in the hope that we recover with a reconnect.
            logger.exception("Serial sensor %s had an error. Reconnecting..." % self.name)
            self.__reconnect()
        return None