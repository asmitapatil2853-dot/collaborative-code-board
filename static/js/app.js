const socket = io();
const usernameInput = document.getElementById("username");
const roomInput = document.getElementById("room");
const joinForm = document.getElementById("joinForm");
const languageSelect = document.getElementById("languageSelect");
const connectionText = document.getElementById("connectionText");
const workspaceLabel = document.getElementById("workspaceLabel");
const saveStatus = document.getElementById("saveStatus");
const commentsList = document.getElementById("commentsList");
const commentCount = document.getElementById("commentCount");
const toast = document.getElementById("toast");
const userRoleBadge = document.getElementById("userRoleBadge");

let currentRoom = "";
let currentUser = usernameInput.value;
let currentRole = "viewer";
let joined = false;
let suppressEditorChange = false;
let toastTimer;
const commentCache = new Map();

const editor = CodeMirror.fromTextArea(document.getElementById("codeEditor"), {
  theme: "dracula",
  lineNumbers: true,
  indentUnit: 4,
  tabSize: 4,
  lineWrapping: true,
  mode: "python",
  readOnly: false
});

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2800);
}

function updateLineCount() {
  const count = editor.lineCount();
  document.getElementById("lineCount").textContent = `${count} ${count === 1 ? "line" : "lines"}`;
}

editor.on("change", () => {
  updateLineCount();
  if (!joined || suppressEditorChange || currentRole === "viewer") return;
  saveStatus.textContent = "Syncing…";
  socket.emit("code_change", {
    room: currentRoom,
    code: editor.getValue(),
    language: languageSelect.value
  });
  saveStatus.textContent = "Changes sent";
});

// Join Workspace
joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const room = roomInput.value.trim();
  if (room.length < 2) {
    showToast("Enter a valid workspace ID.");
    return;
  }
  currentRoom = room;
  joined = false;
  socket.emit("join_workspace", { username: currentUser, room: currentRoom });
  workspaceLabel.textContent = currentRoom;
  showToast(`Joining workspace: ${currentRoom}`);
});

// Workspace State Synced
socket.on("workspace_state", (data) => {
  currentRoom = data.room;
  currentRole = data.role || "viewer";
  joined = true;
  suppressEditorChange = true;
  editor.setValue(data.code || "");
  languageSelect.value = data.language || "python";
  suppressEditorChange = false;

  userRoleBadge.textContent = `ROLE: ${currentRole.toUpperCase()}`;
  editor.setOption("readOnly", currentRole === "viewer");

  if(currentRole === "viewer") {
    showToast("Joined as Viewer (Read Only mode)");
  }

  commentCache.clear();
  commentsList.replaceChildren();
  (data.comments || []).slice().reverse().forEach(renderComment);
  updateLineCount();
  saveStatus.textContent = `Saved · ${data.updated_at || "just now"}`;
  workspaceLabel.textContent = currentRoom;
  showToast(`Connected to ${currentRoom}`);

  // व्हर्जन्स लिस्ट लोड करणे
  socket.emit("get_versions", { room: currentRoom });
});

// २. Workspace Share Form Handling
document.getElementById("shareForm").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!joined) {
    showToast("Join a workspace first.");
    return;
  }
  const targetUser = document.getElementById("shareUsername").value.trim();
  const role = document.getElementById("shareRole").value;
  socket.emit("share_workspace", { room: currentRoom, username: targetUser, role: role });
});

socket.on("share_success", (data) => {
  showToast(data.message);
  document.getElementById("shareUsername").value = "";
});

// ३. Code Version History Handling
document.getElementById("saveVersionBtn").addEventListener("click", () => {
  if (!joined) {
    showToast("Join a workspace first.");
    return;
  }
  if (currentRole === "viewer") {
    showToast("Viewers cannot save versions.");
    return;
  }
  const note = prompt("Enter a version tag / note:", "Checkpoint") || "Saved revision";
  socket.emit("save_version", { room: currentRoom, note: note });
});

document.getElementById("refreshVersionsBtn").addEventListener("click", () => {
  if (joined) socket.emit("get_versions", { room: currentRoom });
});

socket.on("version_saved", (data) => {
  showToast(`Version saved by ${data.saved_by}`);
  socket.emit("get_versions", { room: currentRoom });
});

socket.on("versions_list", (data) => {
  const container = document.getElementById("versionsList");
  container.innerHTML = "";
  if (!data.versions || data.versions.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No saved versions yet</p></div>';
    return;
  }
  data.versions.forEach(v => {
    const item = document.createElement("div");
    item.className = "activity-item";
    item.style.justifyContent = "space-between";
    item.innerHTML = `
      <div>
        <strong style="font-size:11px;">${v.note}</strong>
        <small style="font-size:9px; color:#888;">${v.saved_by} · ${v.created_at}</small>
      </div>
      ${currentRole !== "viewer" ? `<button class="ghost-btn" style="padding:2px 6px; font-size:10px;" onclick="restoreVersion(${v.id})">Restore</button>` : ''}
    `;
    container.appendChild(item);
  });
});

window.restoreVersion = function(versionId) {
  if (confirm("Restore this version to the live editor?")) {
    socket.emit("restore_version", { room: currentRoom, version_id: versionId });
  }
};

// इतर बेसिक फंक्शन्स
socket.on("code_changed", (data) => {
  if (!joined) return;
  suppressEditorChange = true;
  const cursor = editor.getCursor();
  editor.setValue(data.code || "");
  try { editor.setCursor(cursor); } catch (_) {}
  suppressEditorChange = false;
  saveStatus.textContent = `Live update · ${data.updated_at}`;
});

document.getElementById("commentForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("commentInput");
  const body = input.value.trim();
  if (!body || !joined) return;
  socket.emit("add_comment", { room: currentRoom, body });
  input.value = "";
});

function renderComment(comment) {
  commentCache.set(comment.id, comment);
  const card = document.createElement("article");
  card.className = "comment-card";
  card.innerHTML = `
    <div class="comment-head"><span class="author">${comment.author}</span><span class="comment-time">${comment.created_at}</span></div>
    <div class="comment-body">${comment.body}</div>
  `;
  commentsList.prepend(card);
  commentCount.textContent = commentCache.size;
}

socket.on("new_comment", renderComment);
socket.on("app_error", (data) => showToast(data.message || "Error"));
socket.on("connect", () => { connectionText.textContent = "Connected"; });
socket.on("disconnect", () => { connectionText.textContent = "Disconnected"; });
