import mysql.connector
from mysql.connector import Error
from tabulate import tabulate
import tkinter as tk
from tkinter import ttk
import csv
import os
from datetime import datetime

class ITSMDatabaseViewer:
    def __init__(self, host, port, database, user, password, show_gui=False):
        self.db_config = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password
        }
        self.show_gui = show_gui
        self.connection = None
        self.cursor = None
        self.current_view = None # Tracks if we are looking at 'tables' or 'data'
        self.current_table = None

        self.pending_inserts = []
        self.pending_deletes = []

    def connect(self):
        """Establishes the database connection."""
        try:
            self.connection = mysql.connector.connect(**self.db_config)
            if self.connection.is_connected():
                print("Connection established.\n")
                self.cursor = self.connection.cursor(dictionary=True)
                return True
        except Error as e:
            print(f"Error while connecting to MySQL: {e}")
            return False

    def get_all_tables(self):
        """Fetches a list of all tables in the database."""
        try:
            self.cursor.execute("SHOW TABLES")
            tables = self.cursor.fetchall()
            # Extract table names from the dictionary values
            return [list(t.values())[0] for t in tables]
        except Error as e:
            print(f"Error fetching tables: {e}")
            return []

    def get_table_data(self, table_name, limit=None):
        """Fetches data from a specific table, optionally with a limit."""
        try:
            query = f"SELECT * FROM {table_name}"
            if limit:
                query += f" LIMIT {limit}"
            self.cursor.execute(query)
            return self.cursor.fetchall()
        except Error as e:
            print(f"Error querying table '{table_name}': {e}")
            return []

    def run(self):
        """Main execution logic based on the show_gui flag."""
        if not self.connect():
            return

        tables = self.get_all_tables()

        if not self.show_gui:
            # --- TERMINAL MODE (show_gui=False) ---
            for table in tables:
                print(f"--- Top 5 records from '{table}' ---")
                records = self.get_table_data(table, limit=5)
                
                if records:
                    headers = list(records[0].keys())
                    rows = [list(row.values()) for row in records]
                    print(tabulate(rows, headers=headers, tablefmt="grid"))
                else:
                    print("No records found.")
                print("\n")
                
            self.close_connection()
            
        else:
            # --- GUI MODE (show_gui=True) ---
            self.build_gui(tables)

    def build_gui(self, tables):
        """Constructs the Tkinter interface."""
        self.root = tk.Tk()
        self.root.title("ITSM Database Viewer")
        self.root.geometry("1200x500")

        # Top Frame for controls
        top_frame = tk.Frame(self.root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        # Back Button
        self.btn_back = ttk.Button(top_frame, text="⬅ Back to Tables", command=self.show_tables_view)
        self.btn_back.pack(side=tk.LEFT)

        # Save Button
        self.btn_save = ttk.Button(top_frame, text="💾 Save Table to CSV", command=self.save_current_table)
        self.btn_save.pack(side=tk.LEFT, padx=10)
        self.btn_save.state(['disabled'])

        # Add Row Button
        self.btn_add = ttk.Button(top_frame, text="➕ Add Row", command=self.add_row_popup)
        self.btn_add.pack(side=tk.LEFT, padx=10)
        self.btn_add.state(['disabled'])

        # Delete Row Button
        self.btn_delete = ttk.Button(top_frame, text="❌ Delete Selected", command=self.delete_selected_rows)
        self.btn_delete.pack(side=tk.LEFT)
        self.btn_delete.state(['disabled'])

        # Save Changes Button (RIGHT SIDE)
        self.btn_commit = ttk.Button(top_frame, text="💾 Save Changes", command=self.commit_changes)
        self.btn_commit.pack(side=tk.RIGHT)
        self.btn_commit.state(['disabled'])

        # Treeview Frame
        tree_frame = tk.Frame(self.root)
        tree_frame.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))

        # Scrollbars and Treeview setup
        self.tree = ttk.Treeview(tree_frame, show="headings", selectmode="extended")
        
        y_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        
        self.tree.configure(yscroll=y_scrollbar.set, xscroll=x_scrollbar.set)
        
        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(expand=True, fill=tk.BOTH)

        # Bind double-click event
        self.tree.bind("<Double-1>", self.on_double_click)

        # Set initial view
        if "incident" in tables:
            self.show_data_view("incident")
        else:
            self.show_tables_view()

        # Handle window close to clean up DB connection
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()

    def show_tables_view(self):
        """Displays the list of all tables."""
        self.current_view = "tables"
        self.btn_back.state(['disabled']) # Disable back button
        self.btn_save.state(['disabled'])
        self.current_table = None

        self.btn_add.state(['disabled'])
        self.btn_delete.state(['disabled'])
        self.btn_commit.state(['disabled'])
        
        # Clear current treeview
        self.tree.delete(*self.tree.get_children())
        
        self.tree["columns"] = ("Table Name",)
        self.tree.heading("Table Name", text="Database Tables (Double-click to open)")
        self.tree.column("Table Name", anchor=tk.W, width=1150)

        tables = self.get_all_tables()
        for table in tables:
            self.tree.insert("", tk.END, values=(table,))

    def show_data_view(self, table_name):
        """Displays all records for a specific table."""
        self.current_view = "data"
        self.current_table = table_name
        self.btn_back.state(['!disabled']) # Enable back button
        self.btn_save.state(['!disabled'])

        self.btn_add.state(['!disabled'])
        self.btn_delete.state(['!disabled'])
        self.btn_commit.state(['!disabled'])
        
        # Clear current treeview
        self.tree.delete(*self.tree.get_children())
        
        records = self.get_table_data(table_name) # No limit passed, fetches all
        
        if not records:
            self.tree["columns"] = ("Message",)
            self.tree.heading("Message", text=f"Data for '{table_name}'")
            self.tree.column("Message", anchor=tk.W, width=1150)
            self.tree.insert("", tk.END, values=("No records found in this table.",))
            return

        headers = list(records[0].keys())
        self.tree["columns"] = headers
        
        for col in headers:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150, anchor=tk.W)

        for row in records:
            str_row = [str(item) for item in row.values()]
            self.tree.insert("", tk.END, values=str_row)

    def on_double_click(self, event):
        """Handles double-click events on the Treeview."""
        if self.current_view == "tables":
            selected_item = self.tree.selection()
            if selected_item:
                # Get the table name from the double-clicked row
                table_name = self.tree.item(selected_item[0], "values")[0]
                self.show_data_view(table_name)

    def add_row_popup(self):
        if not self.current_table:
            return

        popup = tk.Toplevel(self.root)
        popup.title(f"Add Row to {self.current_table}")

        headers = self.tree["columns"]
        entries = {}

        for i, col in enumerate(headers):
            tk.Label(popup, text=col).grid(row=i, column=0, padx=5, pady=5)
            entry = tk.Entry(popup)
            entry.grid(row=i, column=1, padx=5, pady=5)
            entries[col] = entry

        def submit():
            new_row = {col: entries[col].get() for col in headers}
            self.pending_inserts.append(new_row)

            # Show in UI immediately
            self.tree.insert("", tk.END, values=list(new_row.values()))
            popup.destroy()

        tk.Button(popup, text="Add", command=submit).grid(row=len(headers), columnspan=2, pady=10)

    def delete_selected_rows(self):
        selected_items = self.tree.selection()

        if not selected_items:
            return

        for item in selected_items:
            row_values = self.tree.item(item, "values")
            headers = self.tree["columns"]

            row_dict = dict(zip(headers, row_values))
            self.pending_deletes.append(row_dict)

            # Remove from UI immediately
            self.tree.delete(item)

    def commit_changes(self):
        '''Commits all pending inserts and deletes to the database.'''
        if not self.current_table:
            return

        try:
            # INSERTS
            for row in self.pending_inserts:
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["%s"] * len(row))
                values = list(row.values())

                query = f"INSERT INTO {self.current_table} ({cols}) VALUES ({placeholders})"
                self.cursor.execute(query, values)

            # DELETES
            for row in self.pending_deletes:
                conditions = " AND ".join([f"{col}=%s" for col in row.keys()])
                values = list(row.values())

                query = f"DELETE FROM {self.current_table} WHERE {conditions}"
                self.cursor.execute(query, values)

            self.connection.commit()

            print("Changes committed to database.")

            # Clear staging
            self.pending_inserts.clear()
            self.pending_deletes.clear()

        except Error as e:
            print(f"Error committing changes: {e}")
            self.connection.rollback()

    def save_current_table(self):
        """Saves the currently viewed table to a CSV file."""
        if not self.current_table:
            return

        records = self.get_table_data(self.current_table)

        if not records:
            print("No data to save.")
            return

        headers = list(records[0].keys())

        # Create timestamp folder
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_path = os.path.join("saved_tables", timestamp)

        os.makedirs(folder_path, exist_ok=True)

        file_path = os.path.join(folder_path, f"{self.current_table}.csv")

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

                for row in records:
                    writer.writerow(row.values())

            print(f"Table '{self.current_table}' saved to {file_path}")

        except Exception as e:
            print(f"Error saving CSV: {e}")

    def close_connection(self):
        if self.connection and self.connection.is_connected():
            self.cursor.close()
            self.connection.close()

    def on_closing(self):
        """Triggered when the GUI window is closed."""
        self.close_connection()
        self.root.destroy()


if __name__ == '__main__':
    # --- CHANGE THIS FLAG TO TEST TERMINAL VS GUI ---
    # False = Terminal output (Top 5 of all tables)
    # True = GUI Pop-up (Defaults to incident table, fetches all rows)
    TEST_GUI = True

    viewer = ITSMDatabaseViewer(
        host='127.0.0.1',
        port=3307,
        database='cerebree_itsmdb',
        user='root',
        password='admin123',
        show_gui=TEST_GUI
    )
    
    viewer.run()