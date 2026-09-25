const form = document.querySelector('#chat-form');
const input = document.querySelector('#message-input');
const messages = document.querySelector('#messages');
const sendButton = document.querySelector('#send-button');
const clearButton = document.querySelector('#clear-button');
const themeButton = document.querySelector('#theme-button');
const sessionId = `demo-${crypto.randomUUID()}`;

function addMessage(role, text, className = '') {
  const article = document.createElement('article');
  article.className = `message ${role === 'user' ? 'user-message' : 'assistant-message'} ${className}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? '你' : '助手';
  const body = document.createElement('p');
  body.textContent = text;
  article.append(label, body);
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function renderTrace(data) {
  document.querySelector('#trace-empty').hidden = true;
  document.querySelector('#trace-content').hidden = false;
  document.querySelector('#route-value').textContent = data.route;
  document.querySelector('#tool-value').textContent = data.tool_name || '未调用';
  document.querySelector('#latency-value').textContent = `${data.latency_ms.toFixed(2)} ms`;

  const citationBox = document.querySelector('#citations');
  citationBox.replaceChildren();
  if (!data.citations.length) {
    const empty = document.createElement('p');
    empty.textContent = '本次没有知识库引用。';
    citationBox.append(empty);
  } else {
    data.citations.forEach((item) => {
      const citation = document.createElement('div');
      citation.className = 'citation';
      citation.textContent = `${item.source_id}  ${item.title}  ${item.section}  ${item.chunk_id}`;
      citationBox.append(citation);
    });
  }

  const traceList = document.querySelector('#trace-list');
  traceList.replaceChildren();
  data.trace.forEach((item) => {
    const row = document.createElement('li');
    row.textContent = `${item.action || item.step}${item.tool ? `: ${item.tool}` : ''}`;
    traceList.append(row);
  });
}

async function submitQuestion(question) {
  addMessage('user', question);
  const loading = addMessage('assistant', '正在检索可靠证据并选择处理路由。', 'loading-message');
  sendButton.disabled = true;
  sendButton.textContent = '处理中';
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question, session_id: sessionId}),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    loading.remove();
    addMessage('assistant', data.answer);
    renderTrace(data);
  } catch (error) {
    loading.remove();
    addMessage('assistant', `请求失败：${error.message}。请确认本地服务正在运行。`, 'error-message');
  } finally {
    sendButton.disabled = false;
    sendButton.textContent = '发送问题';
    input.focus();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  submitQuestion(question);
});

document.querySelectorAll('[data-question]').forEach((button) => {
  button.addEventListener('click', () => {
    input.value = button.dataset.question;
    input.focus();
  });
});

clearButton.addEventListener('click', async () => {
  await fetch('/api/reset', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId}),
  });
  messages.replaceChildren();
  addMessage('assistant', '已清空本次会话记忆。');
  document.querySelector('#trace-content').hidden = true;
  document.querySelector('#trace-empty').hidden = false;
});

themeButton.addEventListener('click', () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
});

const demoScenario = new URLSearchParams(window.location.search).get('demo');
const demoQuestions = {
  knowledge: '图书馆周末几点开馆？',
  tool: '查询 S1001 的 AP2026001 申请进度',
  unknown: '明天一食堂的菜单是什么？',
};
if (demoQuestions[demoScenario]) {
  submitQuestion(demoQuestions[demoScenario]);
}
if (navigator.webdriver && demoScenario === 'unknown' && window.innerWidth === 500) {
  document.body.classList.add('capture-mobile');
}
