const form = document.getElementById("chatForm");
const messages = document.getElementById("messages");
const rawResponse = document.getElementById("rawResponse");
const apiBase = document.getElementById("apiBase");
const userIdInput = document.getElementById("userId");
const messageInput = document.getElementById("message");

function appendMessage(text, role) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

async function sendMessage(userId, query) {
  const endpoint = `${apiBase.value.replace(/\/$/, "")}/api/chat`;
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: Number(userId), query })
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }

  return res.json();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const userId = userIdInput.value.trim();
  const query = messageInput.value.trim();

  if (!userId || !query) {
    return;
  }

  appendMessage(query, "user");
  messageInput.value = "";

  try {
    const data = await sendMessage(userId, query);
    appendMessage(data.response || "(no response)", "bot");
    rawResponse.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    appendMessage(`Error: ${err.message}`, "bot");
    rawResponse.textContent = String(err);
  }
});
