import pykka
import transitions
#from transitions.extensions import HierarchicalGraphMachine as Machine
from transitions.extensions import HierarchicalMachine as Machine
import time
from datetime import datetime
import logging
import functools
import re

logger = logging.getLogger(__name__)


class StopRepeatException(Exception):
    pass


def repeat(delay=10):
    assert delay >= 0

    def wrap(func):
        @functools.wraps(func)
        def wrapped_func(self, *args, **kwargs):
            try:
                func(self, *args, **kwargs)
            except StopRepeatException:
                pass
            else:
                if delay > 0:
                    self._proxy.do_delay(delay, func.__name__)
                else:
                    function = getattr(self._proxy, func.__name__)
                    function(*args, **kwargs)
        return wrapped_func
    return wrap


def do_repeat():
    def wrap(func):
        @functools.wraps(func)
        def wrapped_func(self, *args, **kwargs):
            try:
                func(self, *args, **kwargs)
            except StopRepeatException:
                pass
            else:
                method = re.sub("on_enter_", "do_repeat_", func.__name__)
                function = getattr(self, method)
                function()
        return wrapped_func
    return wrap


class PoupoolModel(Machine):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("before_state_change", []).extend(["do_cancel", self.__update_state_time])
        super().__init__(
            auto_transitions=False,
            ignore_invalid_triggers=True,
            *args,
            **kwargs
        )
        self.__state_time = None

    def __update_state_time(self):
        self.__state_time = datetime.now()

    def get_time_in_state(self):
        return datetime.now() - self.__state_time


class PoupoolActor(pykka.ThreadingActor):

    def __init__(self):
        super().__init__()
        self._proxy = self.actor_ref.proxy()
        self.__delay_counter = 0

    # def on_failure(self, exception_type, exception_value, traceback):
    #    logger.fatal(exception_type, exception_value, traceback)
    #    get_actor("Filtration").stop()

    def get_actor(self, name):
        fsm = pykka.ActorRegistry.get_by_class_name(name)
        if fsm:
            return fsm[0].proxy()
        logger.critical("Actor %s not found!!!" % name)
        return None

    def do_cancel(self):
        self.__delay_counter += 1

    def do_delay(self, delay, method, *args, **kwargs):
        assert type(method) == str
        self.__delay_counter += 1
        end = time.time() + delay
        self.do_delay_internal(self.__delay_counter, end, method, *args, **kwargs)

    def do_delay_internal(self, counter, end, method, *args, **kwargs):
        if counter == self.__delay_counter:
            if (end - time.time()) > 0:
                time.sleep(0.1)
                self._proxy.do_delay_internal(counter, end, method, *args, **kwargs)
            else:
                func = getattr(self._proxy, method)
                func(*args, **kwargs)
