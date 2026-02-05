def read_book_by_chunk(filename="《诸神愚戏》作者：一月九十秋.txt", chunk_size=100, start_line=32225):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"文件 {filename} 未找到。")
        return

    total_lines = len(lines)
    if start_line < 1 or start_line > total_lines:
        print(f"起始行数无效。当前文件共{total_lines}行。")
        return

    # 从用户指定的行开始拼接内容
    content = ''.join(lines[start_line - 1:])  # Python索引从0开始
    index = 0
    length = len(content)

    print("输入 'c' 并回车以继续阅读下100个字符，输入'q'退出。")
    while index < length:
        cmd = input()
        if cmd.strip().lower() == 'c':
            print(content[index:index+chunk_size])
            index += chunk_size
        elif cmd.strip().lower() == 'q':
            print("已退出阅读。")
            break
        else:
            print("无效输入，请输入'c'继续或'q'退出。")

if __name__ == "__main__":
    try:
        start_line_input = input("请输入起始输出行数（默认为1）：").strip()
        start_line = int(start_line_input) if start_line_input else 1
    except ValueError:
        print("输入无效，默认使用第1行作为起始行。")
        start_line = 1
    read_book_by_chunk(start_line=start_line)