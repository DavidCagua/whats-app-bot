export interface StaffMember {
  id: string;
  business_id: string;
  name: string;
  role: string;
  is_active: boolean | null;
  user_id?: string | null;
  created_at?: Date | null;
  updated_at?: Date | null;
  users?: {
    id: string;
    email: string;
    full_name: string | null;
  } | null;
  user?: {
    id: string;
    email: string;
    name: string | null;
  };
}
