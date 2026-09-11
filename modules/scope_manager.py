# modules/scope_manager.py
class ScopeManager:
    def __init__(self, db_cursor):
        self.cursor = db_cursor

    def get_role_info(self, user_id):
        """Returns a tuple of (role_id, role_name)"""
        query = """
            SELECT r.role_id, r.role_name 
            FROM user_role ur 
            JOIN role r ON ur.role_id = r.role_id 
            WHERE ur.user_id = %s 
            ORDER BY ur.role_id ASC LIMIT 1
        """
        self.cursor.execute(query, (user_id,))
        result = self.cursor.fetchone()
        
        if result:
            return result.get('role_id', 4), result.get('role_name', 'Caller')
        return 4, 'Caller'

    def get_user_role(self, user_id):
        role_id, _ = self.get_role_info(user_id)
        return role_id

    def get_scope_condition(self, user_id, table_alias=""):
        role_id = self.get_user_role(user_id)
        prefix = f"{table_alias}." if table_alias else ""
        
        # --- FIXED: Admins and Managers restricted strictly to their Company ---
        if role_id in [1, 2]: 
            self.cursor.execute("SELECT company_id FROM user WHERE user_id = %s", (user_id,))
            comp_res = self.cursor.fetchone()
            company_id = comp_res['company_id'] if comp_res else 1
            return f"{prefix}company_id = {company_id}"
            
        elif role_id == 3: # Agent Scope - Restricted strictly to their Groups & Assigned Tickets
            self.cursor.execute("SELECT company_id FROM user WHERE user_id = %s", (user_id,))
            comp_res = self.cursor.fetchone()
            company_id = comp_res['company_id'] if comp_res else 1

            self.cursor.execute("SELECT group_id FROM group_member WHERE user_id = %s", (user_id,))
            group_res = self.cursor.fetchall()
            
            # If they have no groups, assign -1 so the SQL matches nothing, safely locking them out
            group_ids = [str(g['group_id']) for g in group_res] if group_res else ['-1']
            group_list = ",".join(group_ids)

            # Allows viewing tickets explicitly assigned to them OR unassigned tickets that map to their groups
            condition = f"""
                {prefix}company_id = {company_id} AND (
                    {prefix}assigned_user_id = {user_id} 
                    OR {prefix}assigned_group_id IN ({group_list})
                    OR ({prefix}assigned_group_id IS NULL AND {prefix}category_id IN (
                        SELECT category_id FROM category WHERE assignment_group_id IN ({group_list})
                    ))
                )
            """
            return condition
            
        elif role_id == 4: # Caller Scope
            return f"{prefix}caller_user_id = {user_id}"
            
        else:
            return f"{prefix}caller_user_id = {user_id}"