# -*- coding: utf-8 -*-
"""用户设置持久化专项测试：API Key 等设置必须存到固定位置，exe 挪走也不丢

运行: python tools/test_settings_persistence.py
"""
import json
import os
import subprocess
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from framework import config  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" ({detail})" if detail else ""))


print("== 用户设置持久化测试 ==")

# 1. 存储位置固定在 %APPDATA%\DesktopPet
path = config._user_settings_path()
appdata = os.environ.get("APPDATA", "")
expected_dir = os.path.join(appdata, config.SETTINGS_APP_DIR)
check("设置文件在 %APPDATA% 固定目录", os.path.dirname(path) == expected_dir,
      path)
check("存储目录已创建", os.path.isdir(expected_dir))

# 2. 读写回环：保存什么、读回什么
before = config.load_user_settings()
config.save_user_settings(**before)
after = config.load_user_settings()
check("读写回环一致", before == after)
check("文件是合法 JSON", isinstance(after, dict))

# 2.5 视觉模型配置（key/url/model）：默认值 + 持久化回环（测完恢复原值）
check("默认视觉模型 glm-4.6v-flash",
      config.VISION_MODEL == "glm-4.6v-flash", config.VISION_MODEL)
before_v = (config.VISION_API_KEY, config.VISION_API_URL, config.VISION_MODEL)
config.save_user_settings(
    vision_key="test-vision-key",
    vision_url="https://example.com/v4/chat/completions",
    vision_model="test-vision-model",
    websearch_key="test-search-key")
config.apply_user_settings()
check("视觉Key持久化加载", config.VISION_API_KEY == "test-vision-key")
check("视觉地址持久化加载",
      config.VISION_API_URL == "https://example.com/v4/chat/completions")
check("视觉模型持久化加载", config.VISION_MODEL == "test-vision-model")
check("搜索Key持久化加载", config.WEBSEARCH_API_KEY == "test-search-key")
config.save_user_settings(vision_key=before_v[0], vision_url=before_v[1],
                          vision_model=before_v[2],
                          websearch_key=before.get("websearch_key", ""))
config.apply_user_settings()
check("视觉配置测后恢复原值",
      (config.VISION_API_KEY, config.VISION_API_URL, config.VISION_MODEL)
      == before_v)
# 本机设置里若本来就存了搜索 Key，"恢复原值"后应该回到那把 Key 而不是空
check("搜索Key测后恢复原值",
      config.WEBSEARCH_API_KEY == before.get("websearch_key", ""))

# 3. 本机已有 API Key 的话，启动加载后应自动切到真实 AI 模式
if before.get("api_key"):
    config.apply_user_settings()
    check("启动加载 API Key", config.DEEPSEEK_API_KEY == before["api_key"])
    check("有 Key 自动启用真实AI", config.USE_REAL_AI is True)
else:
    print("  [SKIP] 本机未存 API Key，跳过加载检查")

# 4. 迁移：换一个全新 APPDATA（模拟 exe 在新电脑/新目录首跑），
#    老位置 data/user_settings.json 的设置应被自动搬过去
legacy_src = os.path.join(PROJECT, "data", "user_settings.json")
tmp_appdata = tempfile.mkdtemp(prefix="pet_settings_test_")
try:
    env = dict(os.environ, APPDATA=tmp_appdata)
    code = (
        "import sys, json; sys.path.insert(0, r'%s')\n"
        "from framework import config\n"
        "p = config._user_settings_path()\n"
        "print(json.dumps({'path': p, 'data': config.load_user_settings()}))"
        % PROJECT
    )
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, timeout=60)
    ok = out.returncode == 0
    check("全新环境读取设置无异常", ok,
          out.stderr.strip().splitlines()[-1] if not ok and out.stderr else "")
    if ok:
        payload = json.loads(out.stdout.strip().splitlines()[-1])
        new_dir = os.path.join(tmp_appdata, config.SETTINGS_APP_DIR)
        check("新环境路径指向临时 APPDATA",
              payload["path"].startswith(new_dir))
        if os.path.exists(legacy_src):
            with open(legacy_src, encoding="utf-8") as f:
                legacy_data = json.load(f)
            check("老位置设置已自动迁移", payload["data"] == legacy_data,
                  str(payload["path"]))
        else:
            print("  [SKIP] 本项目无旧版 data/user_settings.json")
finally:
    import shutil
    shutil.rmtree(tmp_appdata, ignore_errors=True)

# 5. 全新用户（无任何旧版设置）首次保存：文件里应只有写入的键
tmp2 = tempfile.mkdtemp(prefix="pet_settings_empty_")
try:
    env = dict(os.environ, APPDATA=tmp2)
    code = (
        "import sys, json, os; sys.path.insert(0, r'%s')\n"
        "import framework.utils as u\n"
        "u.app_data_dir = lambda: r'%s'   # 模拟无旧版 data 的全新环境\n"
        "from framework import config\n"
        "config.save_user_settings(sound_volume=0.5)\n"
        "print(json.dumps(config.load_user_settings()))"
        % (PROJECT, tmp2)
    )
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, timeout=60)
    check("全新用户首次保存正常",
          out.returncode == 0 and json.loads(out.stdout.strip()) ==
          {"sound_volume": 0.5},
          out.stderr.strip().splitlines()[-1] if out.returncode else "")
finally:
    import shutil
    shutil.rmtree(tmp2, ignore_errors=True)

passed = sum(1 for _, ok in results if ok)
print(f"== 完成: {passed}/{len(results)} 通过 ==")
sys.exit(0 if passed == len(results) else 1)
