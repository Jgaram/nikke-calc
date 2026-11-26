/**
 * 단일 브랜치(master) 규칙을 강제하는 훅. 규칙 자체는 AGENTS.md 「Landing changes」에 있다.
 * nikke-community의 같은 훅(nikke-story → nikke-opt → nikke-community 순으로 다듬어졌다)을 가져와
 * 이 레포의 기본 브랜치 `master`에 맞췄다 (2026-11-26). 고칠 때 저쪽 레포들과 함께 고친다.
 *
 *   node .claude/hooks/single-branch.mjs session-start   세션을 master로 옮기고 최신으로 맞춘다
 *   node .claude/hooks/single-branch.mjs pre-push        master가 아닌 브랜치로 가는 git push를 막는다
 *   node .claude/hooks/single-branch.mjs stop            master에 안 올린 커밋이 있으면 턴을 못 끝낸다
 *
 * 원격 세션(CLAUDE_CODE_REMOTE=true)에서만 동작한다. 로컬 세션은 CLAUDE.md 규칙만 따른다.
 * 훅 자체가 실패하면 작업을 막지 않고 통과시킨다.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const BRANCH = 'master';
const REMOTE = 'origin';
const TRACKING = `${REMOTE}/${BRANCH}`;

const cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd();

function git(args, { timeout = 30_000 } = {}) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    timeout,
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

/** 실패하면 null. `merge-base --is-ancestor`처럼 종료 코드가 답인 명령에도 쓴다. */
function tryGit(args, opts) {
  try {
    return git(args, opts);
  } catch {
    return null;
  }
}

const count = (range) => Number(tryGit(['rev-list', '--count', range]) ?? 0);

/**
 * ref에만 있고 origin/master에 같은 내용(patch-id)으로도 없는 커밋 수. 진짜 "push 안 된 작업"은 이것뿐이다.
 * master를 force push로 다시 쓴 뒤에도 컨테이너가 옛 master를 들고 시작한다 (2026-10-15: 공통 조상 없는 옛 master가
 * "push 안 된 커밋 49개"로 잡혀 세션마다 옛 역사로 옮겨졌다. 49개 모두 내용은 이미 origin/master에 있었다).
 * 그런 ref는 origin/master로 맞춰도 잃는 것이 없다.
 */
const unpushed = (ref) =>
  Number(tryGit(['rev-list', '--count', '--cherry-pick', '--right-only', '--no-merges', `${TRACKING}...${ref}`]) ?? 0);
const dirtyCount = () => (tryGit(['status', '--porcelain']) ?? '').split('\n').filter(Boolean).length;
const currentBranch = () => tryGit(['branch', '--show-current']) || null;
const hasRef = (ref) => tryGit(['rev-parse', '--verify', '--quiet', ref]) !== null;

/** 단일 브랜치로 클론됐어도 origin/* 가 채워지도록 refspec을 명시한다. */
const fetchRefs = (from, to, timeout = 60_000) =>
  tryGit(['fetch', '--quiet', '--prune', REMOTE, `+refs/heads/${from}:refs/remotes/${REMOTE}/${to}`], { timeout }) !==
  null;

/**
 * 얕은 클론에서는 로컬 master · HEAD와 origin/master의 공통 조상이 안 보여 ahead/behind가 양쪽으로 부풀려진다
 * (2026-10-02: 실제로는 origin의 조상인 옛 master가 "push 안 된 커밋 50개"로 보였다). 이어질 때까지 더 받는다.
 */
function connectShallowHistory() {
  if (tryGit(['rev-parse', '--is-shallow-repository']) !== 'true') return;
  const refs = ['HEAD', ...(hasRef(`refs/heads/${BRANCH}`) ? [`refs/heads/${BRANCH}`] : [])];
  for (let i = 0; i < 3; i++) {
    if (refs.every((ref) => tryGit(['merge-base', ref, TRACKING]) !== null)) return;
    const refspec = `+refs/heads/${BRANCH}:refs/remotes/${TRACKING}`;
    if (tryGit(['fetch', '--quiet', '--deepen=300', REMOTE, refspec], { timeout: 60_000 }) === null) return;
  }
}

/**
 * 남은 원격 브랜치도 얕게 받아 오면 origin/master와의 공통 조상이 안 보여 이미 머지된 브랜치가
 * 「머지되지 않음」으로 잡힌다(2026-11-26 nikke-calc: 깊이 5 클론에서 머지된 5개가 그렇게 보였다).
 * 공통 조상이 보일 때까지 그 브랜치만 더 받는다. nikke-opt · nikke-community 판에는 아직 없다.
 */
function connectShallowRef(branch) {
  if (tryGit(['rev-parse', '--is-shallow-repository']) !== 'true') return;
  const ref = `refs/remotes/${REMOTE}/${branch}`;
  for (let i = 0; i < 3; i++) {
    if (tryGit(['merge-base', ref, TRACKING]) !== null) return;
    const refspec = `+refs/heads/${branch}:${ref}`;
    if (tryGit(['fetch', '--quiet', '--deepen=300', REMOTE, refspec], { timeout: 60_000 }) === null) return;
  }
}

const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

// ── SessionStart ────────────────────────────────────────────────────────────

const RULE = [
  '[단일 브랜치 규칙 — AGENTS.md] 이 레포는 master 브랜치 하나만 쓴다.',
  '- 작업은 master에서 하고, push 전에 doclint · snapshot · unittest 셋을 통과시킨 뒤 `git push origin master`로 올린다. PR은 만들지 않는다.',
  '- 세션에 배정된 claude/*·ccr-* 브랜치로는 push하지 않는다. 사용자가 정한 규칙이라 배정 브랜치 지침보다 우선한다.',
].join('\n');

function sessionStart() {
  const notes = [];
  try {
    if (!fetchRefs('*', '*')) {
      notes.push(`- ${REMOTE}에서 브랜치를 받아 오지 못해 master 전환을 건너뛰었다.`);
    } else if (!hasRef(TRACKING)) {
      notes.push(`- 원격에 ${BRANCH} 브랜치가 없다. 사용자에게 알린다.`);
    } else {
      connectShallowHistory();
      notes.push(...moveToMain(), ...remoteBranchNotes());
    }
  } catch (err) {
    notes.push(`- 브랜치 정리 중 오류: ${err.message.split('\n')[0]}`);
  }
  emit({
    hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: [RULE, ...notes].join('\n') },
  });
}

function moveToMain() {
  const current = currentBranch();
  const ahead = unpushed('HEAD');
  const behind = count(`HEAD..${TRACKING}`);
  const dirty = dirtyCount();

  if (current === BRANCH) {
    if (!behind) return [];
    if (!ahead && !dirty && count(`${TRACKING}..HEAD`)) {
      // 해시만 다르고 내용은 모두 origin/master에 있는 옛 역사(force push 이전)다
      git(['reset', '--quiet', '--hard', TRACKING]);
      return ['- 로컬 master가 force push 이전의 옛 역사(내용은 모두 origin/master에 있음)라 origin/master로 맞췄다.'];
    }
    if (ahead || dirty) {
      return [`- master가 origin/master보다 ${behind}커밋 뒤처졌는데 로컬 작업이 있어 그대로 두었다. \`git fetch origin master && git merge origin/master\`으로 맞춘다.`];
    }
    git(['merge', '--ff-only', '--quiet', TRACKING]);
    return [`- master를 origin/master로 fast-forward했다 (+${behind}커밋).`];
  }

  const from = current ?? 'detached HEAD';
  if (ahead || dirty) {
    return [
      `- ${from}에 master에 없는 작업이 있어(커밋 ${ahead}개, 커밋 안 된 변경 ${dirty}개) master로 옮기지 않았다. ` +
        '커밋한 뒤 합친다: `git fetch origin master && git merge origin/master && git push origin HEAD:master && git checkout -B master origin/master`',
    ];
  }
  // 로컬 master에 push 안 된 커밋이 있으면 origin/master로 덮어쓰지 않는다
  // 해시만 다르고 내용이 origin/master에 이미 있는 옛 커밋은 세지 않는다
  const localAhead = hasRef(`refs/heads/${BRANCH}`) ? unpushed(`refs/heads/${BRANCH}`) : 0;
  if (localAhead) {
    git(['checkout', '--quiet', BRANCH]);
    return [`- ${from}에서 master로 옮겼다. 로컬 master에 push 안 된 커밋이 ${localAhead}개 있다: \`git fetch origin master && git merge origin/master && git push origin master\``];
  }
  git(['checkout', '--quiet', '-B', BRANCH, TRACKING]);
  return [`- 배정 브랜치 ${from}에서 master(origin/master)로 옮겼다.`];
}

/** 기본 브랜치가 master인지, master 말고 남은 원격 브랜치가 있는지 알려 준다. */
function remoteBranchNotes() {
  const notes = [];
  const symref = tryGit(['ls-remote', '--symref', REMOTE, 'HEAD']);
  const defaultBranch = symref?.match(/^ref: refs\/heads\/(\S+)\s+HEAD/m)?.[1] ?? null;
  if (defaultBranch && defaultBranch !== BRANCH) {
    notes.push(
      `- GitHub 기본 브랜치가 아직 ${defaultBranch}다. 새 세션은 거기서 클론된다. ` +
        '사용자에게 저장소 Settings → General → Default branch를 master로 바꿔 달라고 알린다.',
    );
  }

  const others = (tryGit(['for-each-ref', '--format=%(refname:strip=3)', `refs/remotes/${REMOTE}/`]) ?? '')
    .split('\n')
    .filter((b) => b && b !== BRANCH && b !== 'HEAD');
  const merged = [];
  const unmerged = [];
  for (const b of others) {
    connectShallowRef(b);
    const isMerged = tryGit(['merge-base', '--is-ancestor', `refs/remotes/${REMOTE}/${b}`, TRACKING]) !== null;
    (isMerged ? merged : unmerged).push(b);
  }
  // GitHub는 기본 브랜치 삭제를 거부한다. 기본 브랜치가 바뀐 뒤에 지운다.
  // 클라우드 세션의 `git push --delete`는 git 프록시가 403으로 거부한다(2026-09-28 확인) — 사용자가 지워야 한다
  const deletable = merged.filter((b) => b !== defaultBranch);
  if (deletable.length) {
    notes.push(
      `- master에 이미 머지된 원격 브랜치가 남아 있다: ${deletable.join(', ')}. ` +
        '클라우드 세션은 원격 브랜치를 지울 수 없으므로(프록시 403), 사용자에게 GitHub의 Branches 화면에서 지워 달라고 알린다.',
    );
  }
  if (unmerged.length) {
    notes.push(`- master에 머지되지 않은 원격 브랜치: ${unmerged.join(', ')}. 내용을 확인하고 어떻게 할지 사용자에게 묻는다.`);
  }
  return notes;
}

// ── PreToolUse(Bash) ────────────────────────────────────────────────────────

/** 값을 따로 받는 git push 옵션. 바로 다음 토큰은 원격 이름도 refspec도 아니다. */
const OPTS_WITH_VALUE = new Set(['-o', '--push-option', '--repo', '--receive-pack', '--exec']);

/** 명령 맨 앞의 `git push`만 본다. 앞에 올 수 있는 셸 키워드·래퍼·환경 변수 대입은 건너뛴다. */
const LEAD = String.raw`(?:(?:if|elif|while|until|do|then|else|time|command|exec|nohup|sudo|!|\(|\{|timeout\s+\S+|[A-Za-z_]\w*=\S*)\s+)*`;
const GIT_PUSH = new RegExp(String.raw`^\s*${LEAD}git(?:\s+-C\s+\S+)?\s+push\b(.*)$`);

/**
 * 명령에서 실제로 실행될 셸 코드만 남긴다.
 * 히어닥 본문과 공백이 든 따옴표 문자열(커밋 메시지 등)은 지우고, 리다이렉션도 뺀다.
 */
function shellCode(command) {
  const kept = [];
  const pending = []; // 닫히기를 기다리는 히어닥 종결자
  for (const line of command.split('\n')) {
    if (pending.length) {
      if (line.trim() === pending[0]) pending.shift();
      continue;
    }
    kept.push(line);
    for (const m of line.matchAll(/(?<!<)<<(?!<)-?\s*(['"]?)([A-Za-z_][\w-]*)\1/g)) pending.push(m[2]);
  }
  return kept
    .join('\n')
    .replace(/(["'])([\s\S]*?)\1/g, (_, quote, body) => (/\s/.test(body) ? ' _ ' : body))
    .replace(/\d*>&\d+|&>>?\s*\S+|\d*>>?\s*\S+|\d*<\s*\S+/g, ' ');
}

/** 명령 안의 git push가 향하는 브랜치 이름들. 삭제와 태그 push는 브랜치를 늘리지 않으므로 뺀다. */
function pushTargets(command, current) {
  const targets = [];
  for (const segment of shellCode(command).split(/&&|\|\||[;|&\n]/)) {
    const m = segment.match(GIT_PUSH);
    if (!m) continue;
    const tokens = m[1]
      .split(/\s+/)
      .map((t) => t.replace(/[)}`]+$/, ''))
      .filter(Boolean);
    if (tokens.some((t) => t === '--delete' || t === '-d' || t === '--tags')) continue;

    const positional = [];
    for (let i = 0; i < tokens.length; i++) {
      if (OPTS_WITH_VALUE.has(tokens[i])) i++;
      else if (!tokens[i].startsWith('-')) positional.push(tokens[i]);
    }
    const refspecs = positional.slice(1); // 첫 번째는 원격 이름
    if (!refspecs.length) {
      targets.push(current);
      continue;
    }
    for (const spec of refspecs) {
      if (spec.startsWith(':')) continue; // `:branch`는 삭제다
      const dst = spec
        .slice(spec.lastIndexOf(':') + 1)
        .replace(/^\+/, '')
        .replace(/^refs\/heads\//, '');
      if (dst.startsWith('refs/tags/')) continue;
      targets.push(dst === 'HEAD' ? current : dst);
    }
  }
  return targets;
}

function prePush(input) {
  const command = input.tool_input?.command ?? '';
  if (!/\bgit\b[\s\S]*\bpush\b/.test(command)) return;
  const bad = [...new Set(pushTargets(command, currentBranch() ?? 'HEAD').filter((t) => t !== BRANCH))];
  if (!bad.length) return;
  emit({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason:
        `단일 브랜치 규칙(AGENTS.md): master가 아닌 브랜치(${bad.join(', ')})로는 push하지 않는다. ` +
        'master로 올린다: `git push origin HEAD:master`',
    },
  });
}

// ── Stop ────────────────────────────────────────────────────────────────────

function stop(input) {
  fetchRefs(BRANCH, BRANCH, 20_000);
  if (!hasRef(TRACKING)) return; // 원격에 master가 없으면 판단할 기준이 없다

  const ahead = count(`${TRACKING}..HEAD`);
  if (ahead) {
    const where = currentBranch() ?? 'detached HEAD';
    if (input.stop_hook_active) {
      // 이미 한 번 막았다. 계속 막으면 턴이 끝나지 않으므로 사용자에게 알리고 보낸다
      emit({ systemMessage: `⚠ master에 올라가지 않은 커밋 ${ahead}개가 ${where}에 남아 있습니다.` });
      return;
    }
    const steps = ['git fetch origin master && git merge origin/master && git push origin HEAD:master'];
    if (where !== BRANCH) steps.push('git checkout -B master origin/master');
    emit({
      decision: 'block',
      reason:
        `단일 브랜치 규칙(AGENTS.md): ${where === BRANCH ? `로컬 ${BRANCH}` : where}에 ${TRACKING}에 없는 커밋이 ${ahead}개 있다. ` +
        `턴을 끝내기 전에 master로 올린다(push 전에 doclint · snapshot · unittest를 통과시킨다):\n  ${steps.join('\n  ')}\n` +
        '올릴 수 없으면 이유를 사용자에게 알린다.',
    });
    return;
  }

  const dirty = dirtyCount();
  if (dirty) {
    emit({ systemMessage: `⚠ 커밋되지 않은 변경이 ${dirty}개 있습니다. 작업이 끝났다면 커밋해서 master에 올려야 남습니다.` });
  }
}

// ── 진입점 ──────────────────────────────────────────────────────────────────

const MODES = { 'session-start': sessionStart, 'pre-push': prePush, stop };
const run = MODES[process.argv[2]];

if (run && process.env.CLAUDE_CODE_REMOTE === 'true') {
  let input = {};
  try {
    input = JSON.parse(readFileSync(0, 'utf8') || '{}');
  } catch {
    /* 입력이 없어도 판단할 수 있다 */
  }
  try {
    run(input);
  } catch (err) {
    process.stderr.write(`single-branch 훅 오류 (${process.argv[2]}): ${err.message}\n`);
    process.exit(1);
  }
}
