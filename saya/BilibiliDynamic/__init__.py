import re
import json
import asyncio

from pathlib import Path
from graia.saya import Saya, Channel
from graia.application import GraiaMiraiApplication
from graia.application.exceptions import UnknownTarget
from graia.scheduler.timers import every_custom_seconds
from graia.scheduler.saya.schema import SchedulerSchema
from graia.application.event.messages import GroupMessage
from graia.application.group import Group, Member, MemberPerm
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.application.event.lifecycle import ApplicationLaunched
from graia.application.message.parser.literature import Literature
from graia.application.event.mirai import BotLeaveEventKick, BotLeaveEventActive
from graia.application.message.elements.internal import Image_NetworkAddress, MessageChain, Plain, Image_UnsafeBytes

from config import yaml_data
from util.text2image import create_image
from util.limit import group_limit_check
from util.UserBlock import group_black_list_block

from .dynamic_shot import get_dynamic_screenshot
from .bilibili_request import dynamic_svr, get_status_info_by_uids

saya = Saya.current()
channel = Channel.current()

if yaml_data['Saya']['BilibiliDynamic']['EnabledProxy']:
    if yaml_data['Saya']['BilibiliDynamic']['Intervals'] < 30:
        print("动态更新间隔时间过短（不得低于30秒），请重新设置")
        exit()
    else:
        TIME_INTERVALS = 1
else:
    if yaml_data['Saya']['BilibiliDynamic']['Intervals'] < 200:
        print("由于你未使用代理，动态更新间隔时间过短（不得低于200秒），请重新设置")
        exit()
    else:
        TIME_INTERVALS = 30

HOME = Path(__file__).parent
DYNAMIC_OFFSET = {}
LIVE_STATUS = {}
NONE = False

head = {
    'user-agent': 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com/'
}
dynamic_list_json = HOME.joinpath('dynamic_list.json')
if dynamic_list_json.exists():
    with dynamic_list_json.open("r") as f:
        dynamic_list = json.load(f)
else:
    with dynamic_list_json.open("w") as f:
        dynamic_list = {
            "subscription": {}
        }
        json.dump(dynamic_list, f, indent=2)


def get_group_sub(groupid):
    num = 0
    for subuid in dynamic_list['subscription']:
        if groupid in dynamic_list['subscription'][subuid]:
            num += 1
    return num


def get_group_sublist(groupid):
    sublist = []
    for subuid in dynamic_list['subscription']:
        if groupid in dynamic_list['subscription'][subuid]:
            sublist.append(subuid)
    return sublist


def get_subid_list():
    '''获取所有的订阅'''
    subid_list = []
    for subid in dynamic_list['subscription']:
        subid_list.append(subid)
    return subid_list


async def add_uid(uid, groupid):

    pattern = re.compile('^[0-9]*$|com/([0-9]*)')
    match = pattern.search(uid)
    if match:
        if match.group(1):
            uid = match.group(1)
        else:
            uid = match.group(0)
    else:
        return Plain(f"请输入正确的 UP UID 或 首页链接")

    r = await dynamic_svr(uid, GraiaMiraiApplication)
    if "cards" in r["data"]:
        up_name = r["data"]["cards"][0]["desc"]["user_profile"]["info"]["uname"]
        uid_sub_group = dynamic_list['subscription'].get(uid, [])
        if groupid in uid_sub_group:
            return Plain(f"本群已订阅 {up_name}（{uid}）")
        else:
            if uid not in dynamic_list['subscription']:
                LIVE_STATUS[uid] = False
                dynamic_list['subscription'][uid] = []
                last_dynid = r["data"]["cards"][0]["desc"]["dynamic_id"]
                DYNAMIC_OFFSET[uid] = last_dynid
            if get_group_sub(groupid) == 8:
                return Plain(f"每个群聊最多仅可订阅 8 个 UP")
            dynamic_list['subscription'][uid].append(groupid)
            with dynamic_list_json.open('w', encoding="utf-8") as f:
                json.dump(dynamic_list, f, indent=2)
            return Plain(f"成功在本群订阅 {up_name}（{uid}）")
    else:
        Plain(f"该UP（{uid}）未发布任何动态，订阅失败")


def remove_uid(uid, groupid):

    pattern = re.compile('^[0-9]*$|com/([0-9]*)')
    match = pattern.search(uid)
    if match:
        if match.group(1):
            uid = match.group(1)
        else:
            uid = match.group(0)
    else:
        return Plain(f"请输入正确的 UP UID 或 首页链接")

    uid_sub_group = dynamic_list['subscription'].get(uid, [])
    if groupid in uid_sub_group:
        dynamic_list['subscription'][uid].remove(groupid)
        if dynamic_list['subscription'][uid] == []:
            del dynamic_list['subscription'][uid]
        with open('./saya/BilibiliDynamic/dynamic_list.json', 'w', encoding="utf-8") as f:
            json.dump(dynamic_list, f, indent=2)
        return Plain(f"退订成功（{uid}）")
    else:
        return Plain(f"本群未订阅该UP（{uid}）")


def delete_uid(uid):
    del dynamic_list['subscription'][uid]
    with open('./saya/BilibiliDynamic/dynamic_list.json', 'w', encoding="utf-8") as f:
        json.dump(dynamic_list, f, indent=2)


@channel.use(ListenerSchema(listening_events=[ApplicationLaunched]))
async def init(app: GraiaMiraiApplication):

    global NONE

    if yaml_data['Saya']['BilibiliDynamic']['Disabled']:
        return

    subid_list = get_subid_list()
    sub_num = len(subid_list)
    if sub_num == 0:
        NONE = True
        await asyncio.sleep(1)
        return app.logger.info(f"[BiliBili推送] 由于未订阅任何账号，本次初始化结束")
    await asyncio.sleep(1)
    app.logger.info(f"[BiliBili推送] 将对 {sub_num} 个账号进行监控")
    info_msg = [f"[BiliBili推送] 将对 {sub_num} 个账号进行监控"]
    data = {"uids": subid_list}
    r = await get_status_info_by_uids(data, app)
    for uid_statu in r["data"]:
        if r["data"][uid_statu]["live_status"] == 1:
            LIVE_STATUS[uid_statu] = True
        else:
            LIVE_STATUS[uid_statu] = False

    i = 1
    for up_id in subid_list:
        res = await dynamic_svr(up_id, app)
        if "cards" in res["data"]:
            last_dynid = res["data"]["cards"][0]["desc"]["dynamic_id"]
            DYNAMIC_OFFSET[up_id] = last_dynid
            up_name = res["data"]["cards"][0]["desc"]["user_profile"]["info"]["uname"]
            if len(str(i)) == 1:
                si = f"  {i}"
            elif len(str(i)) == 2:
                si = f" {i}"
            else:
                si = i
            if LIVE_STATUS.get(up_id, False):
                live_status = " > 已开播"
            else:
                live_status = ""
            info_msg.append(f"    ● {si}  ---->  {up_name}({up_id}){live_status}")
            app.logger.info(f"[BiliBili推送] 正在初始化  ● {si}  ---->  {up_name}({up_id}){live_status}")
            i += 1
        else:
            delete_uid(up_id)
        await asyncio.sleep(TIME_INTERVALS)

    NONE = True
    await asyncio.sleep(1)

    if i-1 != sub_num:
        info_msg.append(f"[BiliBili推送] 共有 {sub_num-i+1} 个账号无法获取最近动态，暂不可进行监控，已从列表中移除")
    for msg in info_msg:
        app.logger.info(msg)

    image = await create_image("\n".join(info_msg), 100)
    await app.sendFriendMessage(yaml_data['Basic']['Permission']['Master'], MessageChain.create([
        Image_UnsafeBytes(image.getvalue())
    ]))


@channel.use(SchedulerSchema(every_custom_seconds(yaml_data['Saya']['BilibiliDynamic']['Intervals'])))
async def update_scheduled(app: GraiaMiraiApplication):

    if yaml_data['Saya']['BilibiliDynamic']['Disabled']:
        return

    if not NONE:
        return app.logger.info("[BiliBili推送] 初始化未完成，终止本次更新")
    elif len(dynamic_list["subscription"]) == 0:
        return app.logger.info(f"[BiliBili推送] 由于未订阅任何账号，本次更新已终止")

    sub_list = dynamic_list["subscription"].copy()
    subid_list = get_subid_list()
    post_data = {"uids": subid_list}
    app.logger.info("[BiliBili推送] 正在检测直播更新")
    live_statu = await get_status_info_by_uids(post_data, app)
    app.logger.info("[BiliBili推送] 直播更新成功")
    for up_id in live_statu["data"]:
        title = live_statu["data"][up_id]["title"]
        room_id = live_statu["data"][up_id]["room_id"]
        room_area = live_statu["data"][up_id]["area_v2_parent_name"] + " / " + live_statu["data"][up_id]["area_v2_name"]
        up_name = live_statu["data"][up_id]["uname"]
        cover_from_user = live_statu["data"][up_id]["cover_from_user"]

        if live_statu["data"][up_id]["live_status"] == 1:
            if LIVE_STATUS[up_id]:
                continue
            else:
                LIVE_STATUS[up_id] = True
                app.logger.info(f"[BiliBili推送] {up_name} 开播了 - {room_area} - {title}")
                for groupid in sub_list[up_id]:
                    try:
                        await app.sendGroupMessage(groupid, MessageChain.create([
                            Plain(f"本群订阅的UP {up_name}（{up_id}）在 {room_area} 开播啦 ！\n"),
                            Plain(title),
                            Image_NetworkAddress(cover_from_user),
                            Plain(f"\nhttps://live.bilibili.com/{room_id}")
                        ]))
                        await asyncio.sleep(0.3)
                    except UnknownTarget:
                        remove_list = []
                        for subid in get_group_sublist(groupid):
                            remove_uid(subid, groupid)
                            remove_list.append(subid)
                        app.logger.info(f"[BiliBili推送] 推送失败，找不到该群 {groupid}，已删除该群订阅的 {len(remove_list)} 个UP")
        else:
            if LIVE_STATUS[up_id]:
                LIVE_STATUS[up_id] = False
                app.logger.info(f"[BiliBili推送] {up_name} 已下播")
                try:
                    for groupid in sub_list[up_id]:
                        await app.sendGroupMessage(groupid, MessageChain.create([
                            Plain(f"本群订阅的UP {up_name}（{up_id}）已下播！")
                        ]))
                        await asyncio.sleep(0.3)
                except UnknownTarget:
                    remove_list = []
                    for subid in get_group_sublist(groupid):
                        remove_uid(subid, groupid)
                        remove_list.append(subid)
                    app.logger.info(f"[BiliBili推送] 推送失败，找不到该群 {groupid}，已删除该群订阅的 {len(remove_list)} 个UP")

    app.logger.info("[BiliBili推送] 正在检测动态更新")
    for up_id in sub_list:
        r = await dynamic_svr(up_id, app)
        if r:
            if "cards" in r["data"]:
                up_name = r["data"]["cards"][0]["desc"]["user_profile"]["info"]["uname"]
                up_last_dynid = r["data"]["cards"][0]["desc"]["dynamic_id"]
                app.logger.debug(f"[BiliBili推送] {up_name}（{up_id}）检测完成")
                if up_last_dynid > DYNAMIC_OFFSET[up_id]:
                    app.logger.info(f"[BiliBili推送] {up_name} 更新了动态 {up_last_dynid}")
                    DYNAMIC_OFFSET[up_id] = up_last_dynid
                    dyn_url_str = r["data"]["cards"][0]["desc"]["dynamic_id_str"]
                    shot_image = await get_dynamic_screenshot(r["data"]["cards"][0]["desc"]["dynamic_id_str"])
                    for groupid in sub_list[up_id]:
                        try:
                            await app.sendGroupMessage(groupid, MessageChain.create([
                                Plain(f"本群订阅的UP {up_name}（{up_id}）更新动态啦！"),
                                Image_UnsafeBytes(shot_image),
                                Plain(f"https://t.bilibili.com/{dyn_url_str}")
                            ]))
                            await asyncio.sleep(0.3)
                        except UnknownTarget:
                            remove_list = []
                            for subid in get_group_sublist(groupid):
                                remove_uid(subid, groupid)
                                remove_list.append(subid)
                            app.logger.info(f"[BiliBili推送] 推送失败，找不到该群 {groupid}，已删除该群订阅的 {len(remove_list)} 个UP")
                        except Exception as e:
                            app.logger.info(f"[BiliBili推送] 推送失败，未知错误 {type(e)}")
                await asyncio.sleep(TIME_INTERVALS)
            else:
                delete_uid(up_id)
                app.logger.info(f"{up_id} 暂时无法监控，已从列表中移除")
                await app.sendFriendMessage(yaml_data['Basic']['Permission']['Master'], MessageChain.create([
                    Plain(f"{up_id} 暂时无法监控，已从列表中移除")
                ]))
        else:
            await app.sendFriendMessage(yaml_data['Basic']['Permission']['Master'], MessageChain.create([
                Plain(f"动态更新失败超过 3 次，已终止本次更新")
            ]))
            break

    return app.logger.info("[BiliBili推送] 本轮检测完成")


@channel.use(ListenerSchema(listening_events=[GroupMessage],
                            inline_dispatchers=[Literature("订阅")],
                            headless_decorators=[group_limit_check(10), group_black_list_block()]))
async def atrep(app: GraiaMiraiApplication, group: Group, member: Member, message: MessageChain):

    if member.permission in [MemberPerm.Administrator, MemberPerm.Owner] or member.id in yaml_data['Basic']['Permission']['Admin']:
        saying = message.asDisplay().split(" ", 1)
        if len(saying) == 2:
            await app.sendGroupMessage(group, MessageChain.create([await add_uid(saying[1], group.id)]))
    else:
        await app.sendGroupMessage(group, MessageChain.create([
            Plain("你没有权限使用该功能！")
        ]))


@channel.use(ListenerSchema(listening_events=[GroupMessage],
                            inline_dispatchers=[Literature("退订")],
                            headless_decorators=[group_limit_check(10), group_black_list_block()]))
async def atrep(app: GraiaMiraiApplication, group: Group, member: Member, message: MessageChain):

    if member.permission in [MemberPerm.Administrator, MemberPerm.Owner] or member.id in yaml_data['Basic']['Permission']['Admin']:
        saying = message.asDisplay().split(" ", 1)
        if len(saying) == 2:
            await app.sendGroupMessage(group, MessageChain.create([remove_uid(saying[1], group.id)]))
    else:
        await app.sendGroupMessage(group, MessageChain.create([
            Plain("你没有权限使用该功能！")
        ]))


@channel.use(ListenerSchema(listening_events=[GroupMessage],
                            inline_dispatchers=[Literature("本群订阅列表")],
                            headless_decorators=[group_limit_check(10), group_black_list_block()]))
async def atrep(app: GraiaMiraiApplication, group: Group, member: Member):

    if member.permission in [MemberPerm.Administrator, MemberPerm.Owner] or member.id in yaml_data['Basic']['Permission']['Admin']:
        sublist = []
        for subid in get_group_sublist(group.id):
            remove_uid(subid, group.id)
            sublist.append(subid)
        sublist_count = len(sublist)
        if sublist_count == 0:
            await app.sendGroupMessage(group, MessageChain.create([Plain(f"本群未订阅任何 UP")]))
        else:
            await app.sendGroupMessage(group, MessageChain.create([
                Plain(f"本群共订阅 {sublist_count} 个 UP\n"),
                Plain("\n".join(sublist))
            ]))
    else:
        await app.sendGroupMessage(group, MessageChain.create([
            Plain("你没有权限使用该功能！")
        ]))


@channel.use(ListenerSchema(listening_events=[BotLeaveEventActive, BotLeaveEventKick]))
async def bot_leave(app: GraiaMiraiApplication, group: Group):
    remove_list = []
    for subid in get_group_sublist(group.id):
        remove_uid(subid, group.id)
        remove_list.append(subid)
    app.logger.info(f"[BiliBili推送] 检测到退群事件 > {group.name}({group.id})，已删除该群订阅的 {len(remove_list)} 个UP")


@channel.use(ListenerSchema(listening_events=[GroupMessage],
                            inline_dispatchers=[Literature("查看动态")],
                            headless_decorators=[group_limit_check(30), group_black_list_block()]))
async def atrep(app: GraiaMiraiApplication, group: Group, message: MessageChain):

    saying = message.asDisplay().split(" ", 1)
    if len(saying) == 2:
        pattern = re.compile('^[0-9]*$|com/([0-9]*)')
        match = pattern.search(saying[1])
        if match:
            if match.group(1):
                uid = match.group(1)
            else:
                uid = match.group(0)
        else:
            return await app.sendGroupMessage(group, MessageChain.create([
                Plain(f"请输入正确的 UP UID 或 首页链接")
            ]))

        res = await dynamic_svr(uid, app)
        if "cards" in res["data"]:
            shot_image = await get_dynamic_screenshot(res["data"]["cards"][0]["desc"]["dynamic_id_str"])
            await app.sendGroupMessage(group, MessageChain.create([
                Image_UnsafeBytes(shot_image)
            ]))
        else:
            await app.sendGroupMessage(group, MessageChain.create([
                Plain(f"该UP未发布任何动态")
            ]))
