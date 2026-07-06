FROM node:20-alpine AS build
WORKDIR /srv
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-alpine
WORKDIR /srv
ENV NODE_ENV=production
COPY --from=build /srv/.next/standalone ./
COPY --from=build /srv/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
