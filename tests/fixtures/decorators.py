def my_decorator(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


@my_decorator
def decorated_function():
    return 42


class MyClass:
    @staticmethod
    def static_method():
        return 1

    @classmethod
    def class_method(cls):
        return 2
