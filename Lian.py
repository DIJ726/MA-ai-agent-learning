

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

from dataclasses import dataclass

class A:
    def __init__(self,x,y):
        self.x = x
        self.y = y
    
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