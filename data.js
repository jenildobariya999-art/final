let users = {}

export function getUser(id) {
  return users[id]
}

export function saveUser(id, fingerprint) {
  users[id] = fingerprint
}
