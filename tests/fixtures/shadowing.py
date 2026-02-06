x = 1
x = 2
x = 3


def process():
    data = fetch()
    data = parse(data)
    data = validate(data)
    return data
