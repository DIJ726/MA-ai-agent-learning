

# def outer(func):
#     def inner(*args, **kwargs):
#         #原函数执行之前出发 
#         value = func(*args, **kwargs)
#         #原函数执行之后出发 
#         return value
#     return inner



# @outer         #立刻执行outer函数，并带入send_wechat函数作为参数
# def send_wechat(to,body):
#     print("微信",to,body)




# if __name__ == '__main__':
#     send_wechat("张三", "你好")

# from dataclasses import dataclass

# class A:
#     def __init__(self,x,y):
#         self.x = x
#         self.y = y
    
    # def __repr__(self):
    #     return f"A(x={self.x}, y={self.y})"
                                #看dataclass的作用
# @dataclass
# class A:
#     x: int
#     y: int

# a = A(1,2)
# print(a)

    # def __lt__(self, other):
    #     return self.x < other.x and self.y < other.y
# @dataclass(order=True)
# class A:
#     x: int
#     y: int

# a = A(1,2)
# b = A(2,3)
# print(a<b)

## dataclass可替代__init__、__repr__、__lt__、__eq__等方法的实现，简化代码编写。


# import asyncio

# async def coro_1(text):
#     print(f"{text} hello 1")
#     await asyncio.sleep(0.1)
#     print(f"{text} hello 2")

# async def coro_2(text):
#     print(f"{text} world 1")
#     print(f"{text} world 2")
#     await asyncio.sleep(0.1)
#     print(f"{text} world 3")

# async def main():
#     task1 = asyncio.create_task(coro_1("T 1"))
#     task2 = asyncio.create_task(coro_2("T 2"))

#     await asyncio.gather(task1, task2)

# if __name__ == '__main__':
#     asyncio.run(main())


def generate_1(text):
    print(f"{text} hello 1")
    yield "wait"
    yield "ready"
    print(f"{text} hello 2")

def generate_2(text):
    print(f"{text} world 1")
    print(f"{text} world 2")
    yield "wait"
    yield "ready"
    print(f"{text} world 3")

class EventLoop:
    def __init__(self):
        self.ready_tasks = []
        self.waiting_tasks = []

    def create_task(self, gen):
        self.ready_tasks.append(gen)

    def run(self):
        while self.ready_tasks or self.waiting_tasks:
            # Move ready tasks from waiting to ready
            for task in self.waiting_tasks[:]:
                try:
                    status = next(task)
                    if status == "ready":
                        self.ready_tasks.append(task)
                        self.waiting_tasks.remove(task)
                except StopIteration:
                    self.waiting_tasks.remove(task)
            # Execute ready tasks
            for task in self.ready_tasks[:]:
                try:
                    status = next(task)
                    if status == "wait":
                        self.waiting_tasks.append(task)
                        self.ready_tasks.remove(task)
                except StopIteration:
                    self.ready_tasks.remove(task)

loop = EventLoop()
loop.create_task(generate_1("T 1"))
loop.create_task(generate_2("T 2"))
loop.run()