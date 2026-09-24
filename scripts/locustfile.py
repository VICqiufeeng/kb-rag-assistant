"""M7 压测：locust 打真实链路（真索引 + 真 Qwen2.5-3B + MySQL 3308）。

先起服务（单进程，模型常驻约 6GB 显存）：
    python -m uvicorn kbra.api:app --app-dir src --host 127.0.0.1 --port 8000
再压（无 Web UI，结果落 CSV 到 locust_report/）：
    python -m locust -f scripts/locustfile.py --headless -u 100 -r 20 -t 2m \\
        --csv locust_report/run100 --host http://127.0.0.1:8000

需要的环境变量：KBRA_LOAD_USER / KBRA_LOAD_PW（压测账号，见 scripts/init_db.py）。
两个接口分开统计：`生成` 走 GPU，`读库` 只走 FastAPI+MySQL，用来把两者的开销拆开看。
"""
import os

from locust import HttpUser, between, task

QUESTION = "关键信息基础设施的个人信息要存在哪里"


class KbraUser(HttpUser):
    wait_between = between(0.1, 0.3)

    def on_start(self) -> None:
        with self.client.post(
            "/api/login",
            json={"username": os.environ["KBRA_LOAD_USER"], "password": os.environ["KBRA_LOAD_PW"]},
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="登录",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"登录失败 HTTP {resp.status_code}")
                self.env.runner.quit()
                return
            self.token = resp.json()["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(9)
    def ask(self) -> None:
        with self.client.post("/api/ask", json={"question": QUESTION},
                              headers=self.headers, catch_response=True, name="生成") as resp:
            if resp.status_code == 200:
                body = resp.json()
                # 拒答也计成功：压测只关心服务在负载下是否稳定返回
                if "answer" not in body:
                    resp.failure("200 但缺 answer 字段")
            else:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:80]}")

    @task(1)
    def history(self) -> None:
        self.client.get("/api/history?limit=5", headers=self.headers, name="读库")
