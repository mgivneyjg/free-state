class StateMachineError(Exception):
    pass


class NoMatchingEdgeError(StateMachineError):
    pass


class MaxStepsExceededError(StateMachineError):
    pass


class UnknownRunError(StateMachineError):
    pass
