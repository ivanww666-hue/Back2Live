# 1. 先按 Ctrl+C 清空当前输入

# 2. 查看会话
tmux ls

# 3. 如果有 trading，连接它
tmux a -t trading   # attach 可以简写为 a

# 4. 如果没有，新建一个
tmux new -s trading
# 5.关闭
Ctrl+d

tmux kill-session -t trading


python main.py --mode live