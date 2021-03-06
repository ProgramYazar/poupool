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


class Light(PoupoolActor):

    STATE_REFRESH_DELAY = 10

    states = ["stop", "on"]

    def __init__(self, encoder, devices):
        super().__init__()
        self.__encoder = encoder
        self.__devices = devices
        # Initialize the state machine
        self.__machine = PoupoolModel(model=self, states=Light.states, initial="stop")

        self.__machine.add_transition("on", "stop", "on")
        self.__machine.add_transition("stop", "on", "stop")

    def on_enter_stop(self):
        logger.info("Entering stop state")
        self.__encoder.light_state("stop")
        self.__devices.get_valve("light").off()

    def on_enter_on(self):
        logger.info("Entering on state")
        self.__encoder.light_state("on")
        self.__devices.get_valve("light").on()
