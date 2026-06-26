const llmChatMessages = document.getElementById("llm-chat-messages");
const llmChatInput = document.getElementById("llm-chat-input");
const llmChatSend = document.getElementById("llm-chat-send");
const llmChatStatus = document.getElementById("llm-chat-status");
const llmModelProfile = document.getElementById("model_profile");

function setChatStatus(text, className = "text-secondary", title = "") {
  if (!llmChatStatus) return;
  llmChatStatus.textContent = text;
  llmChatStatus.className = `small mb-2 ${className}`;
  llmChatStatus.title = title;
}

function addChatMessage(role, text, meta = "") {
  if (!llmChatMessages) return;
  const row = document.createElement("div");
  row.className = `llm-chat-message llm-chat-${role}`;
  const label = document.createElement("div");
  label.className = "llm-chat-label";
  label.textContent = role === "user" ? "You" : "LLM";
  const body = document.createElement("div");
  body.className = "llm-chat-body";
  body.textContent = text;
  row.appendChild(label);
  row.appendChild(body);
  if (meta) {
    const details = document.createElement("div");
    details.className = "llm-chat-meta";
    details.textContent = meta;
    row.appendChild(details);
  }
  llmChatMessages.appendChild(row);
  llmChatMessages.scrollTop = llmChatMessages.scrollHeight;
}

async function sendLlmChatMessage() {
  const message = (llmChatInput?.value || "").trim();
  if (!message) {
    setChatStatus("Enter a message first", "text-warning");
    return;
  }
  addChatMessage("user", message);
  llmChatInput.value = "";
  llmChatSend.disabled = true;
  setChatStatus("Generating...", "text-secondary");
  try {
    const response = await fetch("/api/llm/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message, model_profile: llmModelProfile?.value || null}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const detail = result.message || result.detail || `HTTP ${response.status}`;
      addChatMessage("assistant", detail, "Fallback/error path");
      setChatStatus("LLM failed", "text-danger", detail);
      return;
    }
    const provider = result.provider || result.auth_mode || "model";
    const model = result.model_label || result.deployment || result.model_profile || "selected model";
    const meta = `${model} - ${provider}`;
    addChatMessage("assistant", result.message, meta);
    setChatStatus(result.used_fallback ? "Fallback response" : "LLM OK", result.used_fallback ? "text-warning" : "text-success", meta);
  } catch (error) {
    addChatMessage("assistant", String(error), "Browser/API error");
    setChatStatus("LLM failed", "text-danger", String(error));
  } finally {
    llmChatSend.disabled = false;
    llmChatInput?.focus();
  }
}

if (llmChatSend && llmChatInput) {
  llmChatSend.addEventListener("click", sendLlmChatMessage);
  llmChatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendLlmChatMessage();
    }
  });
}