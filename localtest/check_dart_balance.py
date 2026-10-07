import glob
import sys

PAIRS = {")": "(", "]": "[", "}": "{"}

def check(path):
    src = open(path, encoding='utf-8').read()
    stack = []
    line = 1
    i = 0
    n = len(src)
    problems = []
    while i < n:
        c = src[i]
        if c == '\n':
            line += 1
            i += 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            i = n if j < 0 else j
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            if j < 0:
                problems.append(f'{path}:{line} block comment unclosed')
                break
            line += src.count('\n', i, j)
            i = j + 2
            continue
        if c in ("'", '"'):
            quote = c
            if src[i:i+3] == quote * 3:
                quote = quote * 3
            j = i + len(quote)
            depth_stack = []
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src.startswith(quote, j):
                    break
                if src.startswith('${', j):
                    depth_stack.append('}')
                    j += 2
                    continue
                if depth_stack and src[j] == '{':
                    depth_stack.append('}')
                elif depth_stack and src[j] == '}':
                    depth_stack.pop()
                j += 1
            if j >= n:
                problems.append(f'{path}:{line} string unclosed')
                break
            line += src.count('\n', i, j)
            i = j + len(quote)
            continue
        if c in '([{':
            stack.append((c, line))
        elif c in ')]}':
            if not stack:
                problems.append(f'{path}:{line} extra {c}')
            else:
                opener, _ = stack.pop()
                if opener != PAIRS[c]:
                    problems.append(f'{path}:{line} {c} does not match {opener} at {_}')
        i += 1
    for opener, ln in stack:
        problems.append(f'{path}: unclosed {opener} at {ln}')
    return problems

if __name__ == '__main__':
    all_probs = []
    files = glob.glob('lib/**/*.dart', recursive=True)
    for f in files:
        p = check(f)
        if p:
            print(f'ERR in {f}:')
            for item in p:
                print('  ', item)
            all_probs.extend(p)
    if not all_probs:
        print(f'ALL {len(files)} DART FILES 100% BALANCED!')
    else:
        sys.exit(1)
