FROM node:22.21.1-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY src ./src
COPY public ./public
RUN mkdir -p /app/data && chown node:node /app/data
USER node
ENV PHONE_HTTP_HOST=0.0.0.0 PHONE_HTTP_PORT=3000
EXPOSE 3000
CMD ["node", "src/server.js"]
