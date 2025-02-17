import time
import redis

from redis.exceptions import ConnectionError
from graia.application.group import Group, Member
from graia.application import GraiaMiraiApplication
from graia.broadcast.exceptions import ExecutionStop
from graia.broadcast.builtin.decorators import Depend
from graia.application.message.chain import MessageChain
from graia.application.message.elements.internal import At, Plain

from config import user_black_list

try:
    r = redis.Redis(host='localhost', port=6379, db=6, decode_responses=True)
    r.flushdb()
except ConnectionError:
    print("Redis 服务器（localhost:6379）连接失败，请检查 Redis 服务器是否正常运行")
    exit()
except Exception as e:
    print(f"Redis 遇到未知错误，请检查。\n{e}")
    exit()

BLOCK_LIST = []


def limit_exists(name, limit):

    now_time = int(time.time())
    if r.exists(name):
        last_time, limited = r.get(name).split("_")
        return True, int(last_time) + int(limited) - now_time, limited
    else:
        r.set(name, str(now_time) + "_" + str(limit), ex=limit)
        try:
            BLOCK_LIST.remove(name)
        except ValueError:
            pass
        return False, None, None


def member_limit_check(limit: int):
    '''
    单用户频率限制
    ~~~~~~~~~~~~~~~~~~~~~
    按群用户独立控制
    '''
    async def limit_wrapper(app: GraiaMiraiApplication, group: Group, member: Member):
        name = str(group.id) + "_" + str(member.id)
        limit_blocked, cd, limited = limit_exists(name, limit)
        if member.id in user_black_list:
            raise ExecutionStop()
        if limit_blocked:
            if name not in BLOCK_LIST:
                await app.sendGroupMessage(group, MessageChain.create([
                    At(member.id),
                    Plain(" 超过调用频率限制"),
                    Plain(f"\n你使用的上一个功能需要你冷却 {limited} 秒"),
                    Plain(f"\n剩余 {cd} 秒后可用")
                ]))
                BLOCK_LIST.append(name)
            raise ExecutionStop()
    return Depend(limit_wrapper)


def group_limit_check(limit: int):
    '''
    群频率限制
    ~~~~~~~~~~~~~~~~~~~~~
    按群独立控制
    '''
    async def limit_wrapper(app: GraiaMiraiApplication, group: Group, member: Member):
        name = str(group.id)
        limit_blocked, cd, limited = limit_exists(name, limit)
        if member.id in user_black_list:
            raise ExecutionStop()
        if limit_blocked:
            if name not in BLOCK_LIST:
                await app.sendGroupMessage(group, MessageChain.create([
                    Plain("超过调用频率限制"),
                    Plain(f"\n本群的上一个功能需要冷却 {limited} 秒"),
                    Plain(f"\n剩余 {cd} 秒后可用")
                ]))
                BLOCK_LIST.append(name)
            raise ExecutionStop()
    return Depend(limit_wrapper)


def manual_limit(group, func, limit: int):
    '''
    手动频率限制
    ~~~~~~~~~~~~~~~~~~~~~
    手动控制
    '''
    name = str(group) + "_" + func
    limit_blocked, _, _ = limit_exists(name, limit)
    if limit_blocked:
        raise ExecutionStop()
