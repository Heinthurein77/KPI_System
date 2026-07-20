import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import ErrorCard from "../components/ui/ErrorCard";

export default function ProtectedRoute({ roles, children }) {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;

  if (roles && !roles.includes(user.role)) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-50 px-4">
        <ErrorCard
          statusCode={403}
          heading="Access Denied"
          message="You do not have permission to view this page."
        />
      </div>
    );
  }

  return children;
}
