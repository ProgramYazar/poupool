import pykka
import time
import logging
import datetime
#from transitions.extensions import GraphMachine as Machine
from .actor import PoupoolActor
from .actor import PoupoolModel
from .actor import StopRepeatException, repeat, do_repeat
from .util import Timer

logger = logging.getLogger(__name__)


class Heater(PoupoolActor):

    STATE_REFRESH_DELAY = 10
    HYSTERESIS_DOWN = 0.5
    HYSTERESIS_UP = 2.0

    states = ["stop", "waiting", "heating"]

    def __init__(self, temperature, heater):
        super().__init__()
        self.__temperature = temperature
        self.__heater = heater
        self.__setpoint = 5.0
        # Initialize the state machine
        self.__machine = PoupoolModel(model=self, states=Heating.states, initial="stop")

        self.__machine.add_transition("waiting", ["stop", "heating"], "waiting")
        self.__machine.add_transition("heat", "waiting", "heating")
        self.__machine.add_transition("stop", ["waiting", "heating"], "stop")

    def setpoint(self, value):
        self.__setpoint = value
        logger.info("Setpoint set to %.1f" % self.__setpoint)

    def on_enter_stop(self):
        logger.info("Entering stop state")
        self.__heater.off()

    @do_repeat()
    def on_enter_waiting(self):
        logger.info("Entering waiting state")

    @repeat(delay=STATE_REFRESH_DELAY)
    def do_repeat_waiting(self):
        temp = self.__temperature.value
        if temp < self.__setpoint - Heater.HYSTERESIS_DOWN:
            self._proxy.heat()
            raise StopRepeatException

    @do_repeat()
    def on_enter_heating(self):
        logger.info("Entering heating state")
        self.__heater.on()

    def on_exit_heating(self):
        self.__heater.off()

    @repeat(delay=STATE_REFRESH_DELAY)
    def do_repeat_heating(self):
        temp = self.__temperature.value
        if temp > self.__setpoint + Heater.HYSTERESIS_UP:
            self._proxy.waiting()
            raise StopRepeatException


class Heating(PoupoolActor):

    STATE_REFRESH_DELAY = 10

    states = ["stop", "waiting", "heating"]

    def __init__(self, encoder, devices):
        super().__init__()
        self.__encoder = encoder
        self.__devices = devices
        # Initialize the state machine
        self.__machine = PoupoolModel(model=self, states=Heating.states, initial="stop")

        self.__machine.add_transition("waiting", "stop", "waiting")
        self.__machine.add_transition("heat", "waiting", "heating")
        self.__machine.add_transition("stop", ["waiting", "heating"], "stop")

    def on_enter_stop(self):
        logger.info("Entering stop state")
        self.__encoder.heating_state("stop")
        self.__devices.get_valve("heating").off()

    def on_enter_waiting(self):
        logger.info("Entering waiting state")
        self.__encoder.heating_state("waiting")
        self._proxy.do_delay(10, "heat")

    def on_enter_heating(self):
        logger.info("Entering heating state")
        self.__encoder.heating_state("heating")
        self.__devices.get_valve("heating").on()
