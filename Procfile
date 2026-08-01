# alwaysdata 部署配置
# Python 版本（alwaysdata 支持 3.12）
python: 3.12

# 启动命令（gunicorn 生产级 WSGI 服务器）
# alwaysdata User program 站点用 $IP 和 $PORT 环境变量指定监听地址
# 重要：workers 必须为1，否则 APScheduler 定时任务会在每个worker重复执行
# threads=8 保证并发能力，timeout=300 适配全市场扫描耗时
start: gunicorn app:app --bind $IP:$PORT --workers 1 --threads 8 --timeout 300
