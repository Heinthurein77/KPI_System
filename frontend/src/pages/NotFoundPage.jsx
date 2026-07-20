import ErrorCard from "../components/ui/ErrorCard";

export default function NotFoundPage() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-50 px-4">
      <ErrorCard statusCode={404} heading="Page Not Found" message="The page you're looking for doesn't exist." />
    </div>
  );
}
