x = 10


class MyClass:
    class_var = 20

    def method(self, arg):
        local_var = arg + x
        return local_var


def outer():
    a = 1

    def inner():
        nonlocal a
        a = 2
        return a

    return inner()
