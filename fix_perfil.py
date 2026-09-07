import sqlite3

db_path = r'instance\condoreservas.db'
conn    = sqlite3.connect(db_path)

conn.execute("ALTER TABLE usuarios ADD COLUMN perfil VARCHAR(20) NOT NULL DEFAULT 'admin'")
conn.commit()
conn.close()

print('Coluna adicionada!')