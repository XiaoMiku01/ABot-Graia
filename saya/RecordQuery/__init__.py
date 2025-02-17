import json
import httpx
import asyncio

from graia.saya import Saya, Channel
from graia.application.group import Group, Member
from graia.broadcast.interrupt.waiter import Waiter
from graia.application import GraiaMiraiApplication
from graia.broadcast.interrupt import InterruptControl
from graia.application.event.messages import GroupMessage
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.application.message.parser.literature import Literature
from graia.application.message.elements.internal import Image_UnsafeBytes, MessageChain, Source, Plain, At

from config import yaml_data, group_data
from util.limit import member_limit_check
from util.UserBlock import group_black_list_block

from .draw_record_image import AUTH, DATABASE, draw_r6


saya = Saya.current()
channel = Channel.current()
bcc = saya.broadcast
inc = InterruptControl(bcc)

BINDFILE = DATABASE.joinpath("bind.json")
if BINDFILE.exists():
    with BINDFILE.open("r") as f:
        bind = json.load(f)
else:
    with BINDFILE.open("w") as f:
        bind = {}
        json.dump(bind, f, indent=2)

WAITING = []


@channel.use(ListenerSchema(listening_events=[GroupMessage],
                            inline_dispatchers=[Literature("查战绩", "r6")],
                            headless_decorators=[member_limit_check(60), group_black_list_block()]))
async def main(app: GraiaMiraiApplication, group: Group, member: Member, message: MessageChain, source: Source):

    if yaml_data['Saya']['RecordQuery']['Disabled']:
        return
    elif 'RecordQuery' in group_data[group.id]['DisabledFunc']:
        return

    @Waiter.create_using_function([GroupMessage])
    async def waiter1(waiter1_group: Group, waiter1_member: Member, waiter1_message: MessageChain):
        if all([waiter1_group.id == group.id, waiter1_member.id == member.id]):
            waiter1_saying = waiter1_message.asDisplay()
            if waiter1_saying == "取消":
                return False
            elif waiter1_saying.replace(" ", "") == "":
                await app.sendGroupMessage(group, MessageChain.create([Plain("请不要输入空格")]))
            else:
                return waiter1_saying

    @Waiter.create_using_function([GroupMessage])
    async def confirm(confirm_group: Group, confirm_member: Member, confirm_message: MessageChain, confirm_source: Source):
        if all([confirm_group.id == group.id,
                confirm_member.id == member.id]):
            saying = confirm_message.asDisplay()
            if saying == "是":
                return True
            elif saying == "否":
                return False
            else:
                await app.sendGroupMessage(group, MessageChain.create([
                    At(confirm_member.id),
                    Plain("请发送是或否来进行确认")
                ]), quote=confirm_source)

    saying = message.asDisplay().strip().split(" ", 2)
    print(saying)

    if member.id not in WAITING:
        WAITING.append(member.id)

        # 判断消息内是否包含昵称
        if len(saying) == 2:
            # 判断用户是否在绑定表里
            if message.has(At):
                atid = str(message.getFirst(At).target)
                if atid not in bind:
                    WAITING.remove(member.id)
                    return await app.sendGroupMessage(group, MessageChain.create([
                        At(atid),
                        Plain(f" 暂未绑定账号")
                    ]))
                else:
                    nick_name = bind[atid]
            elif str(member.id) not in bind:
                # 等待输入昵称
                try:
                    await app.sendGroupMessage(group, MessageChain.create([Plain(f"未绑定账号，请输入你的游戏昵称，不支持改绑，请谨慎填写")]))
                    nick_name = await asyncio.wait_for(inc.wait(waiter1), timeout=60)
                    if not nick_name:
                        WAITING.remove(member.id)
                        return await app.sendGroupMessage(group, MessageChain.create([Plain("已取消")]))
                except asyncio.TimeoutError:
                    WAITING.remove(member.id)
                    return await app.sendGroupMessage(group, MessageChain.create([
                        Plain("等待超时")
                    ]), quote=source)

                # 搜索昵称
                async with httpx.AsyncClient(timeout=10, auth=AUTH) as client:
                    resp = await client.get(f"https://api.statsdb.net/r6/pc/player/{nick_name}", allow_redirects=True)
                    player_data = resp.json()

                # 如果搜索到了
                if resp.status_code == 200:
                    # 询问是否绑定
                    try:
                        confirm_wait = await app.sendGroupMessage(group, MessageChain.create([
                            Plain(f"已搜索到用户：{player_data['payload']['user']['nickname']}"),
                            Plain(f"\nUUID：{player_data['payload']['user']['id']}"),
                            Plain("\n是否需要绑定此账号？")
                        ]))
                        if not await asyncio.wait_for(inc.wait(confirm), timeout=15):
                            WAITING.remove(member.id)
                            return await app.sendGroupMessage(group, MessageChain.create([Plain("已取消")]))
                        else:
                            WAITING.remove(member.id)
                            bind[str(member.id)] = player_data["payload"]["user"]["nickname"]
                            with BINDFILE.open('w', encoding="utf-8") as f:
                                json.dump(bind, f, indent=2)
                            return await app.sendGroupMessage(group, MessageChain.create([
                                Plain(f"绑定成功：{nick_name}")
                            ]))
                    except asyncio.TimeoutError:
                        WAITING.remove(member.id)
                        return await app.sendGroupMessage(group, MessageChain.create([
                            Plain("等待超时")
                        ]), quote=confirm_wait.messageId)
                # 如果没搜索到
                elif resp.status_code == 404:
                    WAITING.remove(member.id)
                    return await app.sendGroupMessage(group, MessageChain.create([
                        Plain(f"未搜索到该昵称：{nick_name}")
                    ]))
                # 如果其他错误
                else:
                    WAITING.remove(member.id)
                    return await app.sendGroupMessage(group, MessageChain.create([
                        Plain(f"未知错误：{player_data['message']}")
                    ]))
            else:
                nick_name = bind[str(member.id)]
        else:
            nick_name = saying[2]

        await app.sendGroupMessage(group, MessageChain.create([
            Plain(f"正在查询：{nick_name}")
        ]))
        image = await draw_r6(nick_name)
        if image:
            await app.sendGroupMessage(group, MessageChain.create([
                Image_UnsafeBytes(image)
            ]), quote=source)
        else:
            await app.sendGroupMessage(group, MessageChain.create([
                Plain(f"未搜索到该昵称：{nick_name}")
            ]))
            
        WAITING.remove(member.id)